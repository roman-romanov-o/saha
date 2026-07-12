"""Configuration settings for Saha orchestrator."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ToolConfig(BaseSettings):
    """Configuration for external tools."""

    model_config = SettingsConfigDict(env_prefix="SAHA_TOOL_")

    ruff_enabled: bool = True
    ruff_config_path: Path | None = None

    ty_enabled: bool = True
    ty_strict: bool = False

    complexity_enabled: bool = True
    complexity_threshold: int = 15

    playwright_enabled: bool = False
    playwright_headless: bool = True

    pytest_enabled: bool = True
    pytest_args: list[str] = Field(default_factory=lambda: ["-v"])


class AgentRunnerConfig(BaseModel):
    """Configuration for which runner an agent should use."""

    runner: Literal["claude", "codex", "gemini", "mock"] = "claude"
    variant: str | None = None  # Agent variant (e.g., "playwright" for execution-qa-playwright)
    model: str | None = None  # Override model for this agent
    timeout: int = 300


class AgentsConfig(BaseSettings):
    """Per-agent runner configuration.

    Allows specifying different runners for different agents:
    - Use Claude for implementation (tool-heavy)
    - Use Codex for implementation or QA (agentic CLI)
    - Use Gemini for QA (can be cheaper, good for verification)
    - Use Playwright-enabled variant only when UI testing needed
    """

    model_config = SettingsConfigDict(env_prefix="SAHA_AGENT_")

    # Default configuration for all agents
    default_runner: Literal["claude", "codex", "gemini", "mock"] = "claude"

    # Per-agent overrides
    implementer: AgentRunnerConfig = Field(default_factory=AgentRunnerConfig)
    qa: AgentRunnerConfig = Field(default_factory=AgentRunnerConfig)
    code_quality: AgentRunnerConfig = Field(default_factory=AgentRunnerConfig)
    manager: AgentRunnerConfig = Field(default_factory=AgentRunnerConfig)
    dod: AgentRunnerConfig = Field(default_factory=AgentRunnerConfig)

    def get_config(self, agent_name: str) -> AgentRunnerConfig:
        """Get configuration for a specific agent.

        Args:
            agent_name: Agent name (e.g., "execution-qa", "execution-implementer").

        Returns:
            AgentRunnerConfig for the agent.
        """
        # Normalize agent name to config key
        name_map = {
            "execution-implementer": "implementer",
            "execution-qa": "qa",
            "execution-code-quality": "code_quality",
            "execution-manager": "manager",
            "execution-dod": "dod",
        }
        key = name_map.get(agent_name, agent_name.replace("execution-", "").replace("-", "_"))

        if hasattr(self, key):
            config = getattr(self, key)
            assert isinstance(config, AgentRunnerConfig)
            return config

        # Return default config with default runner
        return AgentRunnerConfig(runner=self.default_runner)


class HookConfig(BaseSettings):
    """Configuration for hooks."""

    model_config = SettingsConfigDict(env_prefix="SAHA_HOOK_")

    ntfy_enabled: bool = True
    ntfy_topic: str = "sahaidachny"
    ntfy_server: str = "https://ntfy.sh"
    ntfy_token: str | None = None  # Access token for ntfy authentication
    ntfy_user: str | None = None  # Username for basic auth (alternative to token)
    ntfy_password: str | None = None  # Password for basic auth


class Settings(BaseSettings):
    """Main settings for Saha orchestrator."""

    model_config = SettingsConfigDict(
        env_prefix="SAHA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore unknown env vars from .env files
    )

    state_dir: Path = Path(".sahaidachny")
    task_base_path: Path = Path("docs/tasks")
    max_iterations: int = 10
    max_retries_per_phase: int = 3

    runner: Literal["claude", "codex", "gemini", "mock"] = "claude"
    claude_model: str = "sonnet"
    claude_timeout: int = 300
    # Budget for running the project's own test command (a real suite can far
    # exceed the LLM call budget), kept separate from claude_timeout.
    test_command_timeout: int = 600
    claude_dangerously_skip_permissions: bool = False
    codex_model: str | None = None
    codex_sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = "workspace-write"
    codex_dangerously_bypass_sandbox: bool = False
    gemini_model: str = "gemini-2.5-pro"
    gemini_timeout: int = 300

    plugin_path: Path = Path("claude_plugin")
    agents_path: Path = Path(".claude/agents")  # Agent spec location (Claude Code compatible)

    verbose: bool = False
    dry_run: bool = False

    tools: ToolConfig = Field(default_factory=ToolConfig)
    hooks: HookConfig = Field(default_factory=HookConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)

    def get_state_file(self, task_id: str) -> Path:
        """Get the state file path for a task."""
        return self.state_dir / f"{task_id}-execution-state.yaml"

    def get_task_path(self, task_id: str) -> Path:
        """Get the task folder path."""
        return self.task_base_path / task_id

    def get_agent_path(self, agent_name: str, variant: str | None = None) -> Path:
        """Get the path to a native Claude Code agent spec file.

        Agent names with underscores are converted to hyphens to match
        Claude Code's naming convention.

        Args:
            agent_name: Base agent name (e.g., "execution_qa" or "execution-qa").
            variant: Optional variant suffix (e.g., "playwright" for "execution-qa-playwright").

        Returns:
            Path to the agent spec file.
        """
        normalized_name = agent_name.replace("_", "-")
        if variant:
            normalized_name = f"{normalized_name}-{variant}"
        return self.agents_path / f"{normalized_name}.md"

    def get_agent_runner_config(self, agent_name: str) -> AgentRunnerConfig:
        """Get the runner configuration for a specific agent.

        Args:
            agent_name: Agent name (e.g., "execution-qa").

        Returns:
            AgentRunnerConfig with runner type, variant, and timeout.
        """
        return self.agents.get_config(agent_name)
