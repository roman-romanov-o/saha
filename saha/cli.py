"""Saha CLI - Agentic loop orchestrator.

This is the main entry point for the saha CLI. Commands are organized into modules:
- saha/commands/execution.py: Agentic loop commands (run, resume, status, tools, clean, version)
- saha/commands/plugin.py: Plugin management commands (plugin, claude)
"""

import typer

from saha.commands.execution import register_execution_commands
from saha.commands.plugin import register_plugin_commands
from saha.commands.stats import register_stats_command

app = typer.Typer(
    name="saha",
    help="Agentic loop orchestrator for Sahaidachny task execution.",
    no_args_is_help=True,
)

# Register command groups
register_execution_commands(app)
register_plugin_commands(app)
register_stats_command(app)


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
