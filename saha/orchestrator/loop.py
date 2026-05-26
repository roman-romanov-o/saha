"""Main agentic loop orchestrator."""

import logging
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from saha.config.settings import Settings
from saha.hooks.registry import HookRegistry
from saha.logging import (
    console,
    log_agent_prompt,
    log_iteration_complete,
    log_iteration_start,
    log_phase_complete,
    log_phase_failed,
    log_phase_start,
    log_task_complete,
    log_task_failed,
    log_task_stopped,
    log_token_usage,
)
from saha.models.result import (
    CodeQualityResult,
    QAResult,
    ResultStatus,
    SubagentResult,
    TestCritiqueResult,
)
from saha.models.state import ExecutionState, LoopPhase, PhaseTokenUsage, StepStatus
from saha.orchestrator.artifact_bundler import ArtifactBundler, ArtifactView
from saha.orchestrator.plan_progress import PlanProgressUpdater
from saha.orchestrator.state import StateManager
from saha.runners.base import Runner, RunnerResult
from saha.runners.registry import RunnerRegistry
from saha.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class InterruptHandler:
    """Handles interrupt signals to enable graceful shutdown."""

    def __init__(self) -> None:
        self.interrupt_count = 0
        self.original_handler: Any = None

    def __enter__(self) -> "InterruptHandler":
        """Set up signal handler."""
        self.interrupt_count = 0
        self.original_handler = signal.signal(signal.SIGINT, self._signal_handler)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object
    ) -> None:
        """Restore original signal handler."""
        if self.original_handler is not None:
            signal.signal(signal.SIGINT, self.original_handler)

    def _signal_handler(self, signum: int, frame: object) -> None:
        """Handle interrupt signal."""
        self.interrupt_count += 1
        if self.interrupt_count == 1:
            console.print("\n[yellow]⚠ Interrupt received. Finishing current phase...[/yellow]")
            console.print("[yellow]Press Ctrl+C again to force quit immediately.[/yellow]")
            raise KeyboardInterrupt
        elif self.interrupt_count >= 2:
            console.print("\n[red]⚠ Force quit requested. Exiting immediately...[/red]")
            sys.exit(1)

    def was_interrupted(self) -> bool:
        """Check if an interrupt was received."""
        return self.interrupt_count > 0


@dataclass
class LoopConfig:
    """Configuration for a single loop run."""

    task_id: str
    task_path: Path
    max_iterations: int = 10
    enabled_tools: list[str] | None = None
    playwright_enabled: bool = False
    verification_scripts: list[Path] | None = None


class AgenticLoop:
    """Orchestrates the agentic implementation loop.

    The loop follows this flow:
    1. Run Implementation Subagent → produces code diff
    2. Run Test Critique Subagent → analyzes test quality for hollow tests
    3. If tests are hollow (D/F) → back to step 1 with fix info
    4. Run QA Subagent → verifies DoD with tools
    5. If DoD not achieved → back to step 1 with fix info
    6. Run Code Quality Subagent → checks Ruff, ty, complexity
    7. If quality fails → back to step 1 with fix info
    8. Run Manager Subagent → updates task status
    9. Run DoD Subagent → checks if task is complete
    10. If complete → end, else → back to step 1

    Supports multiple LLM backends via RunnerRegistry, allowing different
    agents to use different runners (e.g., Claude for implementation, Gemini for QA).
    """

    def __init__(
        self,
        runner: Runner,
        tool_registry: ToolRegistry,
        hook_registry: HookRegistry,
        state_manager: StateManager,
        settings: Settings,
        runner_registry: RunnerRegistry | None = None,
    ):
        self._runner = runner  # Default runner for backwards compatibility
        self._runner_registry = runner_registry
        self._tools = tool_registry
        self._hooks = hook_registry
        self._state_manager = state_manager
        self._settings = settings
        self._bundler: ArtifactBundler | None = None

    def _get_bundler(self, task_path: Path) -> ArtifactBundler:
        """Get the artifact bundler, creating one for this task path if needed.

        The bundler is cached per task_path so its mtime-keyed file cache
        survives across phases of the same iteration (and across iterations).
        """
        if self._bundler is None or self._bundler.task_path != task_path:
            self._bundler = ArtifactBundler(task_path)
        return self._bundler

    def _base_context(
        self,
        state: ExecutionState,
        config: LoopConfig,
        view: ArtifactView,
    ) -> dict[str, Any]:
        """Build the shared base context for any phase.

        Bundles task artifacts (filtered by view + status) into the prompt
        context so verifier subagents don't have to re-read the task folder.
        """
        artifacts = self._get_bundler(config.task_path).load(view)
        return {
            "task_id": config.task_id,
            "task_path": str(config.task_path),
            "iteration": state.current_iteration,
            "artifacts": artifacts.model_dump(exclude_none=True, mode="json"),
        }

    def _get_runner_for_agent(self, agent_name: str) -> Runner:
        """Get the appropriate runner for an agent.

        Uses the RunnerRegistry if available, otherwise falls back to default runner.

        Args:
            agent_name: Name of the agent (e.g., "execution-qa").

        Returns:
            Runner instance to use for this agent.
        """
        if self._runner_registry:
            return self._runner_registry.get_runner_for_agent(agent_name)
        return self._runner

    def _record_phase_usage(
        self,
        state: ExecutionState,
        phase: LoopPhase,
        agent_name: str,
        runner: Runner,
        result: RunnerResult,
        started_at: datetime,
    ) -> None:
        """Append per-phase token usage to the current iteration record."""
        iteration = state.current_iteration_record
        if iteration is None:
            return

        completed_at = datetime.now()
        usage = result.token_usage or {}
        iteration.token_usage.append(
            PhaseTokenUsage(
                phase=phase,
                agent_name=agent_name,
                runner_name=runner.get_name(),
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=max(0.0, (completed_at - started_at).total_seconds()),
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                cache_read_input_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
                cache_write_input_tokens=int(usage.get("cache_write_input_tokens", 0) or 0),
                reasoning_tokens=int(usage.get("reasoning_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens") or result.tokens_used or 0),
            )
        )

    def _get_agent_path(self, agent_name: str) -> Path:
        """Get the path to an agent spec, considering variants.

        Checks settings for agent-specific variant configuration.

        Args:
            agent_name: Base agent name (e.g., "execution_qa").

        Returns:
            Path to the agent spec file.
        """
        normalized_name = agent_name.replace("_", "-")
        config = self._settings.get_agent_runner_config(normalized_name)
        return self._settings.get_agent_path(agent_name, variant=config.variant)

    def run(self, config: LoopConfig) -> ExecutionState:
        """Run the agentic loop for a task.

        Always starts fresh - use resume() to continue from saved state.
        """
        # Delete any existing state and create fresh
        self._state_manager.delete(config.task_id)
        state = self._state_manager.create(
            task_id=config.task_id,
            task_path=config.task_path,
            max_iterations=config.max_iterations,
            enabled_tools=config.enabled_tools or self._tools.list_available(),
        )

        console.print(f"\n[task]{'═' * 50}[/task]")
        console.print(f"[task]▶ STARTING TASK: {config.task_id}[/task]")
        console.print(f"[task]  Path: {config.task_path}[/task]")
        console.print(f"[task]  Max iterations: {config.max_iterations}[/task]")
        console.print(f"[task]{'═' * 50}[/task]")
        self._hooks.trigger("loop_start", state=state)

        with InterruptHandler() as interrupt_handler:
            try:
                while self._should_continue(state):
                    # Check for interrupt before starting new iteration
                    if interrupt_handler.was_interrupted():
                        raise KeyboardInterrupt

                    state = self._run_iteration(state, config, interrupt_handler)

                self._finalize(state)

            except KeyboardInterrupt:
                logger.warning("Interrupt received. Stopping agentic loop gracefully.")
                self._handle_interrupt(state, config, interrupt_handler)

            except Exception as e:
                logger.exception(f"Loop failed with error: {e}")
                self._state_manager.mark_failed(state, str(e))
                self._hooks.trigger("loop_error", state=state, error=str(e))

        return state

    def resume(self, task_id: str) -> ExecutionState:
        """Resume a previously started loop."""
        state = self._state_manager.load(task_id)
        if state is None:
            raise LoopError(f"No saved state found for task: {task_id}")

        if state.current_phase in (LoopPhase.COMPLETED, LoopPhase.FAILED):
            raise LoopError(f"Task {task_id} is already {state.current_phase.value}")

        logger.info(f"Resuming loop for task: {task_id} at phase: {state.current_phase}")

        config = LoopConfig(
            task_id=task_id,
            task_path=state.task_path,
            max_iterations=state.max_iterations,
            enabled_tools=state.enabled_tools,
        )

        return self.run(config)

    def _should_continue(self, state: ExecutionState) -> bool:
        """Check if the loop should continue."""
        if state.current_phase in (LoopPhase.COMPLETED, LoopPhase.FAILED, LoopPhase.STOPPED):
            return False

        if state.current_iteration >= state.max_iterations:
            logger.warning(f"Max iterations ({state.max_iterations}) reached")
            return False

        return True

    def _handle_interrupt(
        self, state: ExecutionState, config: LoopConfig, interrupt_handler: InterruptHandler
    ) -> None:
        """Handle a user interrupt (Ctrl+C) gracefully."""
        reason = "Interrupted by user"

        # Attempt to update task status with manager, unless already in/after manager
        # Use a short timeout to avoid hanging during cleanup
        if self._should_run_manager_on_interrupt(state):
            if interrupt_handler.interrupt_count >= 2:
                logger.warning("Force quit requested. Skipping manager update.")
            else:
                console.print(
                    "[yellow]Updating task artifacts (press Ctrl+C again to skip)...[/yellow]"
                )
                try:
                    self._run_manager(state, config, None, None, timeout=10)
                except KeyboardInterrupt:
                    logger.warning("Manager update skipped by user.")
                except Exception as e:
                    logger.warning(f"Manager update during interrupt failed: {e}")

        self._state_manager.mark_stopped(state, reason)
        self._finalize(state)

    def _should_run_manager_on_interrupt(self, state: ExecutionState) -> bool:
        """Determine if manager should run when interrupting."""
        if state.current_iteration == 0:
            return False

        if state.current_phase in (
            LoopPhase.MANAGER,
            LoopPhase.DOD_CHECK,
            LoopPhase.COMPLETED,
            LoopPhase.FAILED,
        ):
            return False

        iteration = state.current_iteration_record
        if iteration:
            for step in iteration.steps:
                if step.phase == LoopPhase.MANAGER and step.status == StepStatus.COMPLETED:
                    return False

        return True

    def _prepare_plan_progress(
        self,
        state: ExecutionState,
        config: LoopConfig,
    ) -> tuple[PlanProgressUpdater | None, Path | None]:
        """Select the active implementation phase for progress updates."""
        updater = PlanProgressUpdater(config.task_path)
        selection = updater.select_active_phase(state)
        if selection is None:
            return None, None
        if selection.updated_context:
            self._state_manager.save(state)
        return updater, selection.phase_path

    def _update_plan_progress(
        self,
        updater: PlanProgressUpdater | None,
        phase_path: Path | None,
        loop_phase: LoopPhase,
        status_kind: str,
        iteration: int,
        note: str | None = None,
        update_status_line: bool = True,
    ) -> None:
        """Best-effort update of the plan execution progress table."""
        if updater is None or phase_path is None:
            return
        try:
            updater.update_execution_progress(
                phase_path,
                loop_phase,
                status_kind,
                iteration,
                note=note,
                update_status_line=update_status_line,
            )
        except Exception as exc:  # pragma: no cover - best effort update
            logger.debug(f"Plan progress update failed: {exc}")

    def _run_iteration(
        self,
        state: ExecutionState,
        config: LoopConfig,
        interrupt_handler: InterruptHandler,
    ) -> ExecutionState:
        """Run a single iteration of the loop."""
        iteration = state.start_iteration()
        log_iteration_start(iteration.iteration, state.max_iterations)
        self._hooks.trigger("iteration_start", state=state, iteration=iteration)

        plan_updater, plan_phase_path = self._prepare_plan_progress(state, config)

        # Phase 1: Implementation
        impl_result = self._run_implementation(state, config, plan_updater, plan_phase_path)
        if not impl_result.succeeded:
            self._run_manager_on_iteration_stop(
                state,
                config,
                plan_updater,
                plan_phase_path,
                stopped_at_phase=LoopPhase.IMPLEMENTATION,
                stop_reason=impl_result.error or "Implementation failed",
                impl_result=impl_result,
            )
            self._state_manager.fail_phase(
                state, LoopPhase.IMPLEMENTATION, impl_result.error or "Implementation failed"
            )
            return state

        # Check for interrupt after implementation
        if interrupt_handler.was_interrupted():
            raise KeyboardInterrupt

        # Track files changed for progress visibility
        if impl_result.structured_output:
            iteration.files_changed = impl_result.structured_output.get("files_changed", [])
            iteration.files_added = impl_result.structured_output.get("files_added", [])
            logger.debug(f"Implementation structured output: {impl_result.structured_output}")
        else:
            logger.warning("Implementation agent did not return structured JSON output")
        state.last_agent_output = impl_result.output
        self._state_manager.save(state)

        # Phase 2: Test Critique (analyze test quality before running)
        critique_result = self._run_test_critique(
            state, config, impl_result, plan_updater, plan_phase_path
        )
        if not critique_result.passed:
            # Loop back with fix info - tests are hollow
            state.context["fix_info"] = critique_result.fix_info
            iteration.fix_info = critique_result.fix_info
            self._state_manager.save(state)
            self._hooks.trigger(
                "test_critique_failed", state=state, critique_result=critique_result
            )
            self._run_manager_on_iteration_stop(
                state,
                config,
                plan_updater,
                plan_phase_path,
                stopped_at_phase=LoopPhase.TEST_CRITIQUE,
                stop_reason=critique_result.fix_info or "Test critique failed",
                impl_result=impl_result,
                critique_result=critique_result,
            )
            return state

        # Check for interrupt after test critique
        if interrupt_handler.was_interrupted():
            raise KeyboardInterrupt

        iteration.test_critique_passed = True

        # Phase 3: QA Verification
        qa_result = self._run_qa(state, config, impl_result, plan_updater, plan_phase_path)
        if not qa_result.dod_achieved:
            # Loop back with fix info
            state.context["fix_info"] = qa_result.fix_info
            iteration.fix_info = qa_result.fix_info
            self._state_manager.save(state)
            self._hooks.trigger("qa_failed", state=state, qa_result=qa_result)
            self._run_manager_on_iteration_stop(
                state,
                config,
                plan_updater,
                plan_phase_path,
                stopped_at_phase=LoopPhase.QA,
                stop_reason=qa_result.fix_info or "QA failed",
                impl_result=impl_result,
                critique_result=critique_result,
                qa_result=qa_result,
            )
            return state

        # Check for interrupt after QA
        if interrupt_handler.was_interrupted():
            raise KeyboardInterrupt

        iteration.dod_achieved = True

        # Phase 4: Code Quality
        quality_result = self._run_code_quality(
            state, config, impl_result, plan_updater, plan_phase_path
        )
        if not quality_result.passed:
            # Loop back with fix info
            state.context["fix_info"] = quality_result.fix_info
            iteration.fix_info = quality_result.fix_info
            self._state_manager.save(state)
            self._hooks.trigger("quality_failed", state=state, quality_result=quality_result)
            self._run_manager_on_iteration_stop(
                state,
                config,
                plan_updater,
                plan_phase_path,
                stopped_at_phase=LoopPhase.CODE_QUALITY,
                stop_reason=quality_result.fix_info or "Code quality failed",
                impl_result=impl_result,
                critique_result=critique_result,
                qa_result=qa_result,
                quality_result=quality_result,
            )
            return state

        # Check for interrupt after code quality
        if interrupt_handler.was_interrupted():
            raise KeyboardInterrupt

        iteration.quality_passed = True

        # Phase 5: Manager updates
        self._run_manager(
            state,
            config,
            plan_updater,
            plan_phase_path,
            impl_result=impl_result,
            critique_result=critique_result,
            qa_result=qa_result,
            quality_result=quality_result,
        )

        # Check for interrupt after manager
        if interrupt_handler.was_interrupted():
            raise KeyboardInterrupt

        # Phase 6: DoD Check
        task_complete = self._run_dod_check(state, config, plan_updater, plan_phase_path)
        if task_complete:
            self._state_manager.mark_completed(state)

        iteration.completed_at = datetime.now()
        log_iteration_complete(
            iteration.iteration,
            dod_achieved=iteration.dod_achieved,
            quality_passed=iteration.quality_passed,
        )
        self._state_manager.save(state)
        self._hooks.trigger("iteration_complete", state=state, iteration=iteration)

        return state

    def _run_manager_on_iteration_stop(
        self,
        state: ExecutionState,
        config: LoopConfig,
        plan_updater: PlanProgressUpdater | None,
        plan_phase_path: Path | None,
        stopped_at_phase: LoopPhase,
        stop_reason: str | None = None,
        impl_result: SubagentResult | None = None,
        critique_result: TestCritiqueResult | None = None,
        qa_result: QAResult | None = None,
        quality_result: CodeQualityResult | None = None,
    ) -> None:
        """Best-effort manager update when iteration stops before DoD."""
        try:
            self._run_manager(
                state,
                config,
                plan_updater,
                plan_phase_path,
                impl_result=impl_result,
                critique_result=critique_result,
                qa_result=qa_result,
                quality_result=quality_result,
                stopped_at_phase=stopped_at_phase,
                stop_reason=stop_reason,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.warning(f"Manager update after {stopped_at_phase.value} stop failed: {exc}")
        finally:
            # Keep loop semantics stable for retries by restoring the stopped phase.
            if state.current_phase not in (
                LoopPhase.FAILED,
                LoopPhase.COMPLETED,
                LoopPhase.STOPPED,
            ):
                state.current_phase = stopped_at_phase
                self._state_manager.save(state)

    def _run_implementation(
        self,
        state: ExecutionState,
        config: LoopConfig,
        plan_updater: PlanProgressUpdater | None,
        plan_phase_path: Path | None,
    ) -> SubagentResult:
        """Run the implementation subagent."""
        self._state_manager.update_phase(state, LoopPhase.IMPLEMENTATION)
        log_phase_start("Implementation", config.task_id)
        self._hooks.trigger("implementation_start", state=state)
        self._update_plan_progress(
            plan_updater,
            plan_phase_path,
            LoopPhase.IMPLEMENTATION,
            "in_progress",
            state.current_iteration,
            note="implementation running",
        )

        agent_name = "execution-implementer"
        agent_path = self._get_agent_path("execution_implementer")
        runner = self._get_runner_for_agent(agent_name)
        console.print(f"[cyan]→ Using runner: {runner.get_name()}[/cyan]")

        # Build context for the agent
        context = self._base_context(state, config, ArtifactView.IMPLEMENTER)
        context["fix_info"] = state.context.get("fix_info")

        # Build the prompt
        prompt = self._build_implementation_prompt(state, config)
        log_agent_prompt("Implementation", prompt)

        started_at = datetime.now()
        result = runner.run_agent(agent_path, prompt, context)
        self._record_phase_usage(
            state, LoopPhase.IMPLEMENTATION, agent_name, runner, result, started_at
        )
        log_token_usage("Implementation", result.token_usage, result.tokens_used)

        if result.success:
            self._state_manager.complete_phase(state, LoopPhase.IMPLEMENTATION, "Code implemented")
            log_phase_complete("Implementation")
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.IMPLEMENTATION,
                "passed",
                state.current_iteration,
                note="implementation complete",
                update_status_line=False,
            )
            return SubagentResult(
                agent_name="implementation",
                status=ResultStatus.SUCCESS,
                output=result.output,
                structured_output=result.structured_output or {},
                tokens_used=result.tokens_used,
            )
        else:
            log_phase_failed("Implementation", result.error or "Unknown error")
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.IMPLEMENTATION,
                "failed",
                state.current_iteration,
                note=result.error or "implementation failed",
                update_status_line=False,
            )
            return SubagentResult(
                agent_name="implementation",
                status=ResultStatus.FAILURE,
                error=result.error,
                tokens_used=result.tokens_used,
            )

    def _run_test_critique(
        self,
        state: ExecutionState,
        config: LoopConfig,
        impl_result: SubagentResult,
        plan_updater: PlanProgressUpdater | None,
        plan_phase_path: Path | None,
    ) -> TestCritiqueResult:
        """Run test critique agent to analyze test quality before QA.

        This phase detects hollow tests that would give false confidence.
        If tests are hollow (score D or F), QA should not run.
        """
        self._state_manager.update_phase(state, LoopPhase.TEST_CRITIQUE)
        log_phase_start("Test Critique", config.task_id)
        self._hooks.trigger("test_critique_start", state=state)
        self._update_plan_progress(
            plan_updater,
            plan_phase_path,
            LoopPhase.TEST_CRITIQUE,
            "in_progress",
            state.current_iteration,
            note="test critique running",
        )

        agent_name = "execution-test-critique"
        agent_path = self._get_agent_path("execution_test_critique")
        runner = self._get_runner_for_agent(agent_name)
        console.print(f"[cyan]→ Using runner: {runner.get_name()}[/cyan]")

        if not agent_path.exists():
            logger.warning(f"Test critique agent not found at {agent_path}, skipping")
            self._state_manager.complete_phase(state, LoopPhase.TEST_CRITIQUE)
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.TEST_CRITIQUE,
                "skipped",
                state.current_iteration,
                note="agent not configured",
                update_status_line=False,
            )
            return TestCritiqueResult(
                status=ResultStatus.SUCCESS,
                passed=True,
                test_quality_score="B",
                summary="Test critique agent not configured, skipping",
            )

        # Extract files from implementation output for focused analysis
        files_changed = []
        files_added = []
        if impl_result.structured_output:
            files_changed = impl_result.structured_output.get("files_changed", [])
            files_added = impl_result.structured_output.get("files_added", [])

        context = self._base_context(state, config, ArtifactView.TEST_CRITIQUE)
        context["files_changed"] = files_changed
        context["files_added"] = files_added

        prompt = self._build_test_critique_prompt(state, config, files_changed + files_added)
        log_agent_prompt("Test Critique", prompt)

        started_at = datetime.now()
        result = runner.run_agent(agent_path, prompt, context)
        self._record_phase_usage(
            state, LoopPhase.TEST_CRITIQUE, agent_name, runner, result, started_at
        )
        log_token_usage("Test Critique", result.token_usage, result.tokens_used)

        if result.success and result.structured_output:
            critique_passed = result.structured_output.get("critique_passed", True)
            score = result.structured_output.get("test_quality_score", "C")
            fix_info = result.structured_output.get("fix_info") if not critique_passed else None

            if critique_passed:
                log_phase_complete("Test Critique", f"Score: {score}")
                self._state_manager.complete_phase(state, LoopPhase.TEST_CRITIQUE)
                self._update_plan_progress(
                    plan_updater,
                    plan_phase_path,
                    LoopPhase.TEST_CRITIQUE,
                    "passed",
                    state.current_iteration,
                    note=f"score {score}",
                    update_status_line=False,
                )
            else:
                log_phase_failed("Test Critique", f"Score: {score} - tests are hollow")
                state.record_step(
                    LoopPhase.TEST_CRITIQUE,
                    StepStatus.FAILED,
                    error=fix_info or "Test quality check failed",
                )
                self._state_manager.save(state)
                self._update_plan_progress(
                    plan_updater,
                    plan_phase_path,
                    LoopPhase.TEST_CRITIQUE,
                    "failed",
                    state.current_iteration,
                    note=f"score {score}",
                    update_status_line=False,
                )

            return TestCritiqueResult(
                status=ResultStatus.SUCCESS if critique_passed else ResultStatus.FAILURE,
                passed=critique_passed,
                test_quality_score=score,
                tests_analyzed=result.structured_output.get("tests_analyzed", 0),
                hollow_tests=result.structured_output.get("hollow_tests", 0),
                issues=result.structured_output.get("issues", []),
                summary=result.structured_output.get("summary", ""),
                fix_info=fix_info,
            )
        else:
            # Agent failed - don't block, but warn
            logger.warning(f"Test critique agent failed: {result.error}, proceeding")
            self._state_manager.complete_phase(state, LoopPhase.TEST_CRITIQUE)
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.TEST_CRITIQUE,
                "skipped",
                state.current_iteration,
                note="agent error",
                update_status_line=False,
            )
            return TestCritiqueResult(
                status=ResultStatus.SUCCESS,
                passed=True,
                test_quality_score="C",
                summary=f"Agent error: {result.error}, proceeding with caution",
            )

    def _build_test_critique_prompt(
        self,
        state: ExecutionState,
        config: LoopConfig,
        files: list[str],
    ) -> str:
        """Build the prompt for the test critique agent."""
        test_files = [f for f in files if "test" in f.lower() or f.endswith("_test.py")]
        prod_files = [f for f in files if f not in test_files]

        parts = [
            f"Analyze test quality AND completeness for task: {config.task_id} (iteration {state.current_iteration}).",
            "",
            "Task artifacts (user stories, code changes, test specs) are bundled in Static artifacts. "
            "Use the bundle as authoritative — only Read the actual test source files in the project.",
            "",
        ]

        if test_files:
            parts.append("Test files to analyze:")
            parts.extend(f"  - {f}" for f in test_files)
        else:
            parts.append(
                "No new test files this iteration — search the project for existing tests covering the changed code."
            )
        if prod_files:
            parts.append("")
            parts.append("Production files changed:")
            parts.extend(f"  - {f}" for f in prod_files)

        parts.extend(
            [
                "",
                "## Phase 1: Completeness (most important)",
                "Cross-reference acceptance criteria (artifacts.user_stories), planned interfaces "
                "(artifacts.code_changes), and planned test specs (artifacts.test_specs) against the actual test "
                "files. Report `uncovered_acceptance_criteria`, `uncovered_code_changes`, `missing_test_specs`. "
                "Missing tests for significant ACs or code changes => critique_passed=false.",
                "",
                "## Phase 2: Quality",
                "Flag over-mocking (>3 mocks/test), mocking-the-SUT, placeholder tests, mock-only assertions, "
                "and missing E2E coverage for key flows.",
                "",
                "## Scoring",
                "6 dimensions: mocking, assertions, structure, coverage, completeness, independence. "
                "Completeness is weighted highest. A/B = proceed; C/D/F = block QA.",
                "",
                "Return JSON: critique_passed, test_quality_score, dimension_scores, "
                "uncovered_acceptance_criteria, uncovered_code_changes, missing_test_specs, fix_info.",
            ]
        )

        return "\n".join(parts)

    def _run_qa(
        self,
        state: ExecutionState,
        config: LoopConfig,
        impl_result: SubagentResult,
        plan_updater: PlanProgressUpdater | None,
        plan_phase_path: Path | None,
    ) -> QAResult:
        """Run the QA subagent with verification tools.

        Automatically selects the Playwright-enabled variant if playwright_enabled
        is True in the config. This keeps Playwright MCP tools out of context
        when not needed.
        """
        self._state_manager.update_phase(state, LoopPhase.QA)
        log_phase_start("QA Verification", config.task_id)
        self._hooks.trigger("qa_start", state=state)
        self._update_plan_progress(
            plan_updater,
            plan_phase_path,
            LoopPhase.QA,
            "in_progress",
            state.current_iteration,
            note="qa running",
        )

        # Select agent variant based on Playwright configuration
        agent_name = "execution-qa"
        if config.playwright_enabled:
            # Use Playwright-enabled variant
            agent_path = self._settings.get_agent_path("execution_qa", variant="playwright")
            logger.info("Using Playwright-enabled QA agent variant")
        else:
            # Check if there's a configured variant in settings
            agent_path = self._get_agent_path("execution_qa")

        runner = self._get_runner_for_agent(agent_name)
        console.print(f"[cyan]→ Using runner: {runner.get_name()}[/cyan]")

        context = self._base_context(state, config, ArtifactView.QA)
        context["implementation_output"] = impl_result.output
        context["verification_scripts"] = [str(s) for s in (config.verification_scripts or [])]
        context["playwright_enabled"] = config.playwright_enabled

        prompt = self._build_qa_prompt(state, config)
        log_agent_prompt("QA", prompt)

        started_at = datetime.now()
        result = runner.run_agent(agent_path, prompt, context)
        self._record_phase_usage(state, LoopPhase.QA, agent_name, runner, result, started_at)
        log_token_usage("QA Verification", result.token_usage, result.tokens_used)

        # Run pytest if enabled
        test_output = ""
        if "pytest" in state.enabled_tools:
            pytest_result = self._tools.run_tool("pytest", config.task_path)
            test_output = pytest_result.stdout

        if result.success:
            # Parse structured output for DoD status
            dod_achieved = (
                result.structured_output.get("dod_achieved", False)
                if result.structured_output
                else False
            )
            fix_info = (
                result.structured_output.get("fix_info") if result.structured_output else None
            )

            self._state_manager.complete_phase(state, LoopPhase.QA)
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.QA,
                "passed" if dod_achieved else "failed",
                state.current_iteration,
                note="criteria met" if dod_achieved else (fix_info or "qa failed"),
                update_status_line=False,
            )

            return QAResult(
                status=ResultStatus.SUCCESS if dod_achieved else ResultStatus.FAILURE,
                dod_achieved=dod_achieved,
                fix_info=fix_info or result.output if not dod_achieved else None,
                test_output=test_output,
            )
        else:
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.QA,
                "failed",
                state.current_iteration,
                note=result.error or "qa error",
                update_status_line=False,
            )
            return QAResult(
                status=ResultStatus.ERROR,
                dod_achieved=False,
                fix_info=result.error,
            )

    def _run_code_quality(
        self,
        state: ExecutionState,
        config: LoopConfig,
        impl_result: SubagentResult,
        plan_updater: PlanProgressUpdater | None,
        plan_phase_path: Path | None,
    ) -> CodeQualityResult:
        """Run code quality subagent on changed files.

        The Code Quality agent intelligently analyzes tool outputs,
        filtering false positives and pre-existing issues.
        """
        self._state_manager.update_phase(state, LoopPhase.CODE_QUALITY)
        log_phase_start("Code Quality", config.task_id)
        self._hooks.trigger("quality_start", state=state)
        self._update_plan_progress(
            plan_updater,
            plan_phase_path,
            LoopPhase.CODE_QUALITY,
            "in_progress",
            state.current_iteration,
            note="quality running",
        )

        agent_name = "execution-code-quality"
        agent_path = self._get_agent_path("execution_code_quality")
        runner = self._get_runner_for_agent(agent_name)
        console.print(f"[cyan]→ Using runner: {runner.get_name()}[/cyan]")

        # Extract changed files from implementation output
        files_changed = []
        files_added = []
        if impl_result.structured_output:
            files_changed = impl_result.structured_output.get("files_changed", [])
            files_added = impl_result.structured_output.get("files_added", [])

        # If no files info, fall back to checking task path
        all_files = files_changed + files_added
        if not all_files:
            logger.warning("No files_changed info from implementation, will scan task path")

        context = self._base_context(state, config, ArtifactView.CODE_QUALITY)
        context["files_changed"] = files_changed
        context["files_added"] = files_added
        context["enabled_tools"] = [
            t for t in state.enabled_tools if t in ("ruff", "ty", "complexity")
        ]

        prompt = self._build_code_quality_prompt(state, config, all_files)
        log_agent_prompt("Code Quality", prompt)

        started_at = datetime.now()
        result = runner.run_agent(agent_path, prompt, context)
        self._record_phase_usage(
            state, LoopPhase.CODE_QUALITY, agent_name, runner, result, started_at
        )
        log_token_usage("Code Quality", result.token_usage, result.tokens_used)

        if result.success and result.structured_output:
            quality_passed = result.structured_output.get("quality_passed", False)
            fix_info = result.structured_output.get("fix_info") if not quality_passed else None

            if quality_passed:
                self._state_manager.complete_phase(state, LoopPhase.CODE_QUALITY)
                self._update_plan_progress(
                    plan_updater,
                    plan_phase_path,
                    LoopPhase.CODE_QUALITY,
                    "passed",
                    state.current_iteration,
                    note="quality passed",
                    update_status_line=False,
                )
            else:
                # Record the failure but don't mark the entire task as failed
                # The loop will continue and retry with fix_info
                state.record_step(
                    LoopPhase.CODE_QUALITY,
                    StepStatus.FAILED,
                    error=fix_info or "Quality check failed",
                )
                self._state_manager.save(state)
                self._update_plan_progress(
                    plan_updater,
                    plan_phase_path,
                    LoopPhase.CODE_QUALITY,
                    "failed",
                    state.current_iteration,
                    note=fix_info or "quality failed",
                    update_status_line=False,
                )

            return CodeQualityResult(
                status=ResultStatus.SUCCESS if quality_passed else ResultStatus.FAILURE,
                passed=quality_passed,
                fix_info=fix_info,
                issues=result.structured_output.get("issues", []),
                files_analyzed=result.structured_output.get("files_analyzed", []),
                blocking_issues_count=result.structured_output.get("blocking_issues_count", 0),
                ignored_issues_count=result.structured_output.get("ignored_issues_count", 0),
            )
        else:
            # Agent failed to run - fall back to passing (don't block on agent errors)
            logger.warning(f"Code quality agent failed: {result.error}, allowing to proceed")
            self._state_manager.complete_phase(state, LoopPhase.CODE_QUALITY)
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.CODE_QUALITY,
                "skipped",
                state.current_iteration,
                note="agent error",
                update_status_line=False,
            )
            return CodeQualityResult(
                status=ResultStatus.SUCCESS,
                passed=True,
                fix_info=None,
            )

    def _build_code_quality_prompt(
        self,
        state: ExecutionState,
        config: LoopConfig,
        files: list[str],
    ) -> str:
        """Build the prompt for the code quality agent."""
        parts = [
            f"Analyze code quality for task: {config.task_id} (iteration {state.current_iteration}).",
        ]
        if files:
            parts.append("Files to analyze:")
            parts.extend(f"  - {f}" for f in files)
        else:
            parts.append("No file list provided — analyze recent changes in the task directory.")
        parts.extend(
            [
                "",
                "Run ruff, ty, complexipy on these files. Filter false positives and pre-existing issues. "
                "Only fail for genuine problems in the changed code.",
                'Return JSON: {"quality_passed": true/false, "fix_info": "..." if failed}',
            ]
        )
        return "\n".join(parts)

    def _run_manager(
        self,
        state: ExecutionState,
        config: LoopConfig,
        plan_updater: PlanProgressUpdater | None,
        plan_phase_path: Path | None,
        impl_result: SubagentResult | None = None,
        critique_result: TestCritiqueResult | None = None,
        qa_result: QAResult | None = None,
        quality_result: CodeQualityResult | None = None,
        stopped_at_phase: LoopPhase | None = None,
        stop_reason: str | None = None,
        timeout: int = 300,
    ) -> None:
        """Run the manager subagent to update task status."""
        self._state_manager.update_phase(state, LoopPhase.MANAGER)
        log_phase_start("Manager", config.task_id)
        self._hooks.trigger("manager_start", state=state)
        self._update_plan_progress(
            plan_updater,
            plan_phase_path,
            LoopPhase.MANAGER,
            "in_progress",
            state.current_iteration,
            note="manager updating",
        )

        agent_name = "execution-manager"
        agent_path = self._get_agent_path("execution_manager")
        runner = self._get_runner_for_agent(agent_name)
        console.print(f"[cyan]→ Using runner: {runner.get_name()}[/cyan]")

        if not agent_path.exists():
            logger.warning(f"Manager agent not found at {agent_path}, skipping")
            self._state_manager.complete_phase(state, LoopPhase.MANAGER)
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.MANAGER,
                "skipped",
                state.current_iteration,
                note="agent not configured",
                update_status_line=False,
            )
            return

        context = self._build_manager_context(
            state,
            config,
            impl_result=impl_result,
            critique_result=critique_result,
            qa_result=qa_result,
            quality_result=quality_result,
            stopped_at_phase=stopped_at_phase,
            stop_reason=stop_reason,
        )

        prompt = self._build_manager_prompt(state, config)
        log_agent_prompt("Manager", prompt)

        started_at = datetime.now()
        result = runner.run_agent(agent_path, prompt, context, timeout=timeout)
        self._record_phase_usage(state, LoopPhase.MANAGER, agent_name, runner, result, started_at)
        log_token_usage("Manager", result.token_usage, result.tokens_used)

        if result.success:
            log_phase_complete("Manager")
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.MANAGER,
                "passed",
                state.current_iteration,
                note="manager complete",
                update_status_line=False,
            )
        else:
            log_phase_failed("Manager", result.error or "Unknown error")
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.MANAGER,
                "failed",
                state.current_iteration,
                note=result.error or "manager failed",
                update_status_line=False,
            )

        self._state_manager.complete_phase(state, LoopPhase.MANAGER)

    def _build_manager_context(
        self,
        state: ExecutionState,
        config: LoopConfig,
        impl_result: SubagentResult | None = None,
        critique_result: TestCritiqueResult | None = None,
        qa_result: QAResult | None = None,
        quality_result: CodeQualityResult | None = None,
        stopped_at_phase: LoopPhase | None = None,
        stop_reason: str | None = None,
    ) -> dict[str, Any]:
        """Build manager context with concrete evidence from this iteration."""
        iteration = state.current_iteration_record
        files_changed = iteration.files_changed if iteration else []
        files_added = iteration.files_added if iteration else []

        implementation_summary: str | None = None
        if impl_result is not None:
            implementation_summary = impl_result.structured_output.get("summary")
            if not implementation_summary and impl_result.output:
                implementation_summary = impl_result.output[:1200]

        iteration_artifacts: dict[str, Any] = {
            "files_changed": files_changed,
            "files_added": files_added,
            "implementation_summary": implementation_summary,
            "test_critique_passed": critique_result.passed if critique_result else None,
            "test_critique_score": critique_result.test_quality_score if critique_result else None,
            "qa_passed": qa_result.dod_achieved if qa_result else None,
            "qa_fix_info": qa_result.fix_info if qa_result else None,
            "quality_passed": quality_result.passed if quality_result else None,
            "quality_blocking_issues": (
                quality_result.blocking_issues_count if quality_result else None
            ),
        }
        if impl_result and impl_result.structured_output:
            iteration_artifacts["implementation_structured_output"] = impl_result.structured_output

        context = self._base_context(state, config, ArtifactView.MANAGER)
        context["current_plan_phase"] = state.context.get("current_plan_phase")
        context["iteration_stop"] = {
            "stopped_at_phase": stopped_at_phase.value if stopped_at_phase else None,
            "reason": stop_reason,
        }
        context["iteration_artifacts"] = iteration_artifacts
        return context

    def _run_dod_check(
        self,
        state: ExecutionState,
        config: LoopConfig,
        plan_updater: PlanProgressUpdater | None,
        plan_phase_path: Path | None,
    ) -> bool:
        """Run the DoD subagent to check if the task is complete.

        This is a CRITICAL gate - the agent MUST actually verify all user stories
        and acceptance criteria are met. No auto-completion fallbacks.
        """
        self._state_manager.update_phase(state, LoopPhase.DOD_CHECK)
        log_phase_start("DoD Check", config.task_id)
        self._hooks.trigger("dod_check_start", state=state)
        self._update_plan_progress(
            plan_updater,
            plan_phase_path,
            LoopPhase.DOD_CHECK,
            "in_progress",
            state.current_iteration,
            note="dod check running",
        )

        agent_name = "execution-dod"
        agent_path = self._get_agent_path("execution_dod")
        runner = self._get_runner_for_agent(agent_name)
        console.print(f"[cyan]→ Using runner: {runner.get_name()}[/cyan]")

        if not agent_path.exists():
            logger.error(f"DoD agent not found at {agent_path}")
            log_phase_failed("DoD Check", "DoD agent not found")
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.DOD_CHECK,
                "failed",
                state.current_iteration,
                note="agent not configured",
                update_status_line=False,
            )
            # Don't auto-complete - this is a configuration error
            return False

        context = self._base_context(state, config, ArtifactView.DOD)
        context["iterations_completed"] = state.current_iteration

        prompt = self._build_dod_prompt(state, config)
        log_agent_prompt("DoD", prompt)

        started_at = datetime.now()
        result = runner.run_agent(agent_path, prompt, context)
        self._record_phase_usage(state, LoopPhase.DOD_CHECK, agent_name, runner, result, started_at)
        log_token_usage("DoD Check", result.token_usage, result.tokens_used)

        if result.success and result.structured_output:
            task_complete = bool(result.structured_output.get("task_complete", False))
            status = "complete" if task_complete else "incomplete"
            remaining = result.structured_output.get("remaining_items", [])

            if task_complete:
                log_phase_complete("DoD Check", f"Task status: {status}")
                self._update_plan_progress(
                    plan_updater,
                    plan_phase_path,
                    LoopPhase.DOD_CHECK,
                    "passed",
                    state.current_iteration,
                    note="task complete",
                    update_status_line=False,
                )
                if plan_updater:
                    plan_updater.mark_all_complete(note="task complete")
            else:
                remaining_str = ", ".join(remaining[:3]) if remaining else "see agent output"
                log_phase_complete("DoD Check", f"Task incomplete: {remaining_str}")
                self._update_plan_progress(
                    plan_updater,
                    plan_phase_path,
                    LoopPhase.DOD_CHECK,
                    "failed",
                    state.current_iteration,
                    note=remaining_str,
                    update_status_line=False,
                )

            self._state_manager.complete_phase(state, LoopPhase.DOD_CHECK)
            return task_complete
        else:
            # Agent failed or didn't return structured output
            error_msg = result.error or "No structured output from DoD agent"
            log_phase_failed("DoD Check", error_msg)
            logger.warning("DoD agent did not return structured output, continuing loop")
            self._update_plan_progress(
                plan_updater,
                plan_phase_path,
                LoopPhase.DOD_CHECK,
                "failed",
                state.current_iteration,
                note=error_msg,
                update_status_line=False,
            )
            # Don't auto-complete - require explicit verification
            return False

    def _finalize(self, state: ExecutionState) -> None:
        """Finalize the loop execution."""
        if state.current_phase == LoopPhase.COMPLETED:
            log_task_complete(state.task_id, state.current_iteration)
            self._hooks.trigger("loop_complete", state=state)
        elif state.current_phase == LoopPhase.FAILED:
            log_task_failed(state.task_id, state.error_message or "Unknown error")
            self._hooks.trigger("loop_failed", state=state)
        elif state.current_phase == LoopPhase.STOPPED:
            log_task_stopped(state.task_id, state.error_message or "Stopped")
            self._hooks.trigger("loop_stopped", state=state)
        else:
            logger.warning(f"Task {state.task_id} stopped at phase: {state.current_phase}")

    def _build_implementation_prompt(
        self,
        state: ExecutionState,
        config: LoopConfig,
    ) -> str:
        """Build the prompt for the implementation agent."""
        parts = [
            f"Implement task: {config.task_id} (iteration {state.current_iteration}).",
        ]

        current_phase = state.context.get("current_plan_phase")
        if current_phase:
            parts.append(f"Current plan phase: {current_phase}")
        parts.append("")

        if state.context.get("fix_info"):
            parts.extend(
                [
                    "## Fix Mode",
                    "",
                    "Previous iteration failed. Focus on fixing these issues:",
                    "",
                    state.context["fix_info"],
                    "",
                    "Run tests after each fix to verify progress.",
                ]
            )
        else:
            parts.extend(
                [
                    "## TDD Cycle",
                    "",
                    "Task artifacts (user stories, code changes, test specs, plan phases) are bundled in "
                    "Static artifacts — use them directly, don't re-read the task folder.",
                    "",
                    "1. **Interfaces** — from `artifacts.code_changes`, create Pydantic models and Protocol classes.",
                    "2. **Tests (Red)** — from `artifacts.test_specs`, write pytest tests; expect them to fail.",
                    "3. **Implementation (Green)** — make the tests pass, then run them to verify.",
                ]
            )

        return "\n".join(parts)

    def _build_qa_prompt(
        self,
        state: ExecutionState,
        config: LoopConfig,
    ) -> str:
        """Build the prompt for the QA agent."""
        parts = [
            f"Verify the implementation for task: {config.task_id} (iteration {state.current_iteration}).",
            "",
            "Acceptance criteria are bundled under `artifacts.user_stories` — verify against them directly.",
            "Check that the implementation matches the requirements, all ACs are met, and tests pass.",
        ]
        if config.verification_scripts:
            parts.append(f"Run verification scripts: {config.verification_scripts}")
        parts.append('Return JSON: {"dod_achieved": true/false, "fix_info": "..." if not achieved}')
        return "\n".join(parts)

    def _build_manager_prompt(
        self,
        state: ExecutionState,
        config: LoopConfig,
    ) -> str:
        """Build the prompt for the manager agent."""
        parts = [
            f"Update task artifacts after iteration {state.current_iteration} for: {config.task_id}.",
        ]
        current_phase = state.context.get("current_plan_phase")
        if current_phase:
            parts.append(f"Current plan phase: {current_phase}")
        parts.append("")

        iteration = state.current_iteration_record
        files_touched: list[str] = []
        if iteration:
            files_touched = sorted(set(iteration.files_changed + iteration.files_added))
        if files_touched:
            parts.append("Files changed this iteration (from runner metadata):")
            parts.extend(f"  - {p}" for p in files_touched[:25])
            if len(files_touched) > 25:
                parts.append(f"  - ... and {len(files_touched) - 25} more")
            parts.append("")

        parts.extend(
            [
                "User stories and plan phases are bundled in `artifacts.user_stories` and "
                "`artifacts.implementation_plan` (Done items as stubs, active items full). "
                "Use the bundle as the source of truth and Edit the matching files on disk to:",
                "- check off newly completed acceptance criteria with [x]",
                "- update story Status when all its ACs are met",
                "- mark completed plan phases",
                "",
                "Use `iteration_artifacts` as the primary evidence. If `iteration_stop.stopped_at_phase` "
                "is set, this iteration ended early — update notes conservatively.",
                "Only mark items as done that are actually implemented. When unsure, leave as pending. "
                "Use status='partial' only when a file update was attempted but failed.",
                "",
                "Return JSON: ",
                '{"status":"success","updates_made":[{"file":"...","change":"...","verified":true}],'
                '"items_completed":["..."],"items_remaining":["..."],"failed_updates":[],"notes":"..."}',
            ]
        )

        return "\n".join(parts)

    def _build_dod_prompt(
        self,
        state: ExecutionState,
        config: LoopConfig,
    ) -> str:
        """Build the prompt for the DoD agent."""
        parts = [
            f"Verify if task is COMPLETE: {config.task_id} (iterations completed: {state.current_iteration}).",
            "",
            "All task artifacts are bundled in Static artifacts (full bodies for the DoD view). "
            "Verify directly against the bundle — do not re-read the task folder.",
            "",
            "Task is COMPLETE only if:",
            "- ALL user stories have status 'Done'",
            "- ALL acceptance criteria are checked [x]",
            "- ALL implementation phases are complete",
            "",
            "If the bundle has `truncated: true`, fall back to reading the specifically "
            "stubbed files listed in `truncation_notes`.",
            "",
            'Return JSON: {"task_complete": true/false, "remaining_items": [...], "reasoning": "..."}',
        ]
        return "\n".join(parts)


class LoopError(Exception):
    """Error during loop execution."""

    pass
