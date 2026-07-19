"""Unit tests for multi-CLI artifact sync helpers."""

from pathlib import Path

import pytest

from saha.commands import plugin as plugin_module
from saha.commands.plugin import _get_command_target_name, sync_artifacts

REPO_ROOT = Path(__file__).resolve().parents[3]


def _create_plugin_tree(base: Path) -> Path:
    plugin = base / "claude_plugin"
    (plugin / "commands").mkdir(parents=True)
    (plugin / "agents").mkdir(parents=True)
    (plugin / "skills" / "task-structure").mkdir(parents=True)
    (plugin / "templates").mkdir(parents=True)
    (plugin / "scripts").mkdir(parents=True)

    (plugin / "commands" / "research.md").write_text("# research")
    (plugin / "commands" / "saha.md").write_text("# saha")
    (plugin / "agents" / "execution-qa.md").write_text("# qa")
    (plugin / "skills" / "task-structure" / "SKILL.md").write_text("# skill")
    (plugin / "templates" / "task.md").write_text("# template")
    (plugin / "scripts" / "init.sh").write_text("#!/usr/bin/env bash\necho init\n")
    (plugin / "settings.json").write_text("{}")
    return plugin


@pytest.mark.parametrize("subdir", ["commands", "agents"])
def test_repo_claude_dir_is_in_sync_with_claude_plugin(subdir: str) -> None:
    """Every committed claude_plugin file must be byte-identical to its .claude copy."""
    plugin_files = sorted((REPO_ROOT / "claude_plugin" / subdir).glob("*.md"))
    assert plugin_files, f"no files found in claude_plugin/{subdir}"
    for src in plugin_files:
        name = _get_command_target_name(src.name) if subdir == "commands" else src.name
        dst = REPO_ROOT / ".claude" / subdir / name
        assert dst.exists(), f"{dst} missing — run `saha sync`"
        assert dst.read_bytes() == src.read_bytes(), f"{dst} differs from {src} — run `saha sync`"


def test_sync_all_targets_creates_expected_layout(tmp_path: Path) -> None:
    plugin = _create_plugin_tree(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = sync_artifacts(target="all", plugin_path=plugin, base_dir=workspace)

    assert result.total_synced > 0
    assert [r.target for r in result.results] == ["claude", "codex", "gemini"]

    assert (workspace / ".claude" / "commands" / "saha:research.md").exists()
    assert (workspace / ".claude" / "commands" / "saha.md").exists()
    assert (workspace / ".codex" / "commands" / "research.md").exists()
    assert (workspace / ".gemini" / "commands" / "research.md").exists()
    assert (workspace / ".codex" / "skills" / "task-structure" / "SKILL.md").exists()
    assert (workspace / ".gemini" / "agents" / "execution-qa.md").exists()


def test_sync_force_false_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    plugin = _create_plugin_tree(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    sync_artifacts(target="codex", plugin_path=plugin, base_dir=workspace)

    command_path = workspace / ".codex" / "commands" / "research.md"
    command_path.write_text("# custom local version")

    result = sync_artifacts(
        target="codex",
        plugin_path=plugin,
        base_dir=workspace,
        force=False,
    )

    assert result.results[0].total_synced == 0
    assert command_path.read_text() == "# custom local version"


def test_sync_force_true_overwrites_changed_files(tmp_path: Path) -> None:
    plugin = _create_plugin_tree(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    sync_artifacts(target="codex", plugin_path=plugin, base_dir=workspace)

    command_path = workspace / ".codex" / "commands" / "research.md"
    command_path.write_text("# custom local version")

    result = sync_artifacts(
        target="codex",
        plugin_path=plugin,
        base_dir=workspace,
        force=True,
    )

    assert result.results[0].total_synced >= 1
    assert command_path.read_text() == "# research"


def test_sync_claude_artifacts_refreshes_changed_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _create_plugin_tree(tmp_path)
    workspace = tmp_path / "workspace"
    claude_dir = workspace / ".claude"
    (claude_dir / "agents").mkdir(parents=True)
    (claude_dir / "agents" / "execution-qa.md").write_text("# stale")

    monkeypatch.setattr(plugin_module, "_find_plugin_path", lambda: plugin)

    result = plugin_module.sync_claude_artifacts(claude_dir)

    assert "execution-qa.md" in result.agents_synced
    assert (claude_dir / "agents" / "execution-qa.md").read_text() == "# qa"
