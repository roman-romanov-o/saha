"""Runner registry for multi-backend LLM support.

This module provides a registry pattern for managing multiple runner backends,
allowing different agents to use different LLM providers (Claude, Codex, Gemini, etc.).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from saha.runners.base import Runner, RunnerResult


class RunnerType(StrEnum):
    """Available runner types."""

    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"
    MOCK = "mock"


@dataclass
class AgentConfig:
    """Configuration for a specific agent.

    Allows customizing which runner and tools an agent uses.
    This enables scenarios like using Codex for QA while Claude for implementation.
    """

    agent_name: str
    runner_type: RunnerType = RunnerType.CLAUDE
    agent_variant: str | None = None  # e.g., "playwright" for execution-qa-playwright
    enabled_tools: list[str] = field(default_factory=list)
    timeout: int = 300
    extra_config: dict[str, Any] = field(default_factory=dict)

    def get_agent_file_name(self) -> str:
        """Get the agent file name, including variant if specified."""
        if self.agent_variant:
            return f"{self.agent_name}-{self.agent_variant}"
        return self.agent_name


class RunnerRegistry:
    """Registry for managing multiple LLM runner backends.

    The registry allows:
    - Registering different runner implementations
    - Configuring which runner to use for each agent
    - Lazy initialization of runners
    """

    def __init__(self) -> None:
        self._runners: dict[RunnerType, Runner] = {}
        self._factories: dict[RunnerType, type[Runner]] = {}
        self._factory_kwargs: dict[RunnerType, dict[str, Any]] = {}
        self._agent_configs: dict[str, AgentConfig] = {}
        self._default_runner_type: RunnerType = RunnerType.CLAUDE

    def register_factory(
        self,
        runner_type: RunnerType,
        factory: type[Runner],
        **kwargs: Any,
    ) -> None:
        """Register a runner factory with optional configuration.

        Args:
            runner_type: Type identifier for this runner.
            factory: Runner class to instantiate.
            **kwargs: Arguments to pass to factory when creating instance.
        """
        self._factories[runner_type] = factory
        self._factory_kwargs[runner_type] = kwargs

    def register_instance(self, runner_type: RunnerType, runner: Runner) -> None:
        """Register an already-instantiated runner.

        Args:
            runner_type: Type identifier for this runner.
            runner: Runner instance.
        """
        self._runners[runner_type] = runner

    def configure_agent(self, config: AgentConfig) -> None:
        """Configure which runner and settings an agent should use.

        Args:
            config: Agent-specific configuration.
        """
        self._agent_configs[config.agent_name] = config

    def get_runner(self, runner_type: RunnerType | None = None) -> Runner:
        """Get a runner instance by type.

        Args:
            runner_type: Type of runner to get. Uses default if None.

        Returns:
            Runner instance.

        Raises:
            ValueError: If runner type is not registered.
        """
        rt = runner_type or self._default_runner_type

        # Return cached instance if available
        if rt in self._runners:
            return self._runners[rt]

        # Create from factory
        if rt not in self._factories:
            raise ValueError(f"No runner registered for type: {rt}")

        factory = self._factories[rt]
        kwargs = self._factory_kwargs.get(rt, {})
        runner = factory(**kwargs)
        self._runners[rt] = runner

        return runner

    def get_runner_for_agent(self, agent_name: str) -> Runner:
        """Get the configured runner for a specific agent.

        Args:
            agent_name: Name of the agent (e.g., "execution-qa").

        Returns:
            Runner instance configured for this agent.
        """
        config = self._agent_configs.get(agent_name)
        runner_type = config.runner_type if config else self._default_runner_type
        return self.get_runner(runner_type)

    def get_agent_config(self, agent_name: str) -> AgentConfig | None:
        """Get configuration for a specific agent.

        Args:
            agent_name: Name of the agent.

        Returns:
            AgentConfig if configured, None otherwise.
        """
        return self._agent_configs.get(agent_name)

    def get_agent_path(
        self,
        agent_name: str,
        agents_dir: Path,
    ) -> Path:
        """Get the path to an agent's spec file, considering variants.

        Args:
            agent_name: Base agent name (e.g., "execution-qa").
            agents_dir: Directory containing agent specs.

        Returns:
            Path to the agent spec file.
        """
        config = self._agent_configs.get(agent_name)
        file_name = config.get_agent_file_name() if config else agent_name
        return agents_dir / f"{file_name}.md"

    def set_default_runner(self, runner_type: RunnerType) -> None:
        """Set the default runner type for unconfigured agents.

        Args:
            runner_type: Default runner type to use.
        """
        self._default_runner_type = runner_type

    def list_available_runners(self) -> list[RunnerType]:
        """List all registered runner types."""
        return list(set(self._runners.keys()) | set(self._factories.keys()))

    def run_agent(
        self,
        agent_name: str,
        agent_path: Path,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> RunnerResult:
        """Run an agent using its configured runner.

        This is a convenience method that:
        1. Looks up the agent's configured runner
        2. Gets the agent config for timeout and other settings
        3. Runs the agent with appropriate configuration

        Args:
            agent_name: Name of the agent to run.
            agent_path: Path to agent spec file.
            prompt: Prompt to send to the agent.
            context: Optional context dict.

        Returns:
            RunnerResult from the agent execution.
        """
        runner = self.get_runner_for_agent(agent_name)
        config = self._agent_configs.get(agent_name)
        timeout = config.timeout if config else 300

        return runner.run_agent(agent_path, prompt, context, timeout=timeout)
