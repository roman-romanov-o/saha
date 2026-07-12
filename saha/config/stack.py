"""Language-agnostic stack profile resolution.

A *stack profile* tells the orchestrator how to build, test, lint, and run the
project under test. It is resolved in priority order:

  1. An explicit ``.sahaidachny/stack.yaml`` (full or partial override).
  2. Auto-detection from marker files (``pyproject.toml``, ``Package.swift`` …).
  3. A neutral fallback where every command is empty.

An **empty command string means "skip that gate"** — the orchestrator never
assumes ``pytest`` (or any other tool). This keeps saha usable for Swift,
Node, Rust, Go, or any toolchain, not just Python.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

STACK_FILENAME = "stack.yaml"
STATE_DIRNAME = ".sahaidachny"


def _glob_to_regex(pattern: str) -> str:
    """Translate a glob (with ``**`` recursive support) to a regex string.

    Path-aware: ``*`` and ``?`` never cross ``/`` boundaries, while ``**/``
    matches zero or more leading path segments.
    """
    out: list[str] = ["(?s:"]
    i, n = 0, len(pattern)
    while i < n:
        if pattern[i : i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
        elif pattern[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append(r")\Z")
    return "".join(out)


def matches_any_glob(path: str, globs: list[str]) -> bool:
    """Return True if ``path`` matches at least one of ``globs``."""
    normalized = path.replace("\\", "/")
    return any(re.match(_glob_to_regex(g), normalized) for g in globs)


class BuildConfig(BaseModel):
    """How to build/compile the project. Empty command = skip the build gate."""

    command: str = ""


class TestConfig(BaseModel):
    """How to run the project's test suite and recognise test files."""

    command: str = ""
    file_globs: list[str] = Field(default_factory=list)


class QualityConfig(BaseModel):
    """How to run linters / type checkers / complexity tools."""

    commands: list[str] = Field(default_factory=list)
    changed_files_only: bool = True


class RunConfig(BaseModel):
    """How to launch the built artifact (used by build/launch verification)."""

    command: str = ""


class StackProfile(BaseModel):
    """Resolved toolchain for the project under test.

    Any section left empty is treated as "skip that gate". The ``language``
    field is advisory metadata passed to agents so they can frame guidance.
    """

    language: str = "unknown"
    build: BuildConfig = Field(default_factory=BuildConfig)
    test: TestConfig = Field(default_factory=TestConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    run: RunConfig = Field(default_factory=RunConfig)

    def to_context(self) -> dict[str, Any]:
        """Flatten the profile into a JSON-serialisable context dict.

        This is what gets injected into ``state.context["stack"]`` and handed
        to verifier subagents, so it must contain only primitives/lists.
        """
        return self.model_dump(mode="json")

    def is_test_file(self, path: str) -> bool:
        """Whether ``path`` looks like a test file under this stack.

        Uses the declared test globs; with none declared, falls back to a
        language-neutral 'test' substring heuristic.
        """
        if self.test.file_globs:
            return matches_any_glob(path, self.test.file_globs)
        return "test" in path.lower()


class StackMarker(BaseModel):
    """Maps marker filenames to the profile they imply when auto-detecting."""

    markers: list[str]
    profile: StackProfile


# Auto-detection table. The first marker present in the repo root wins. Order
# matters only when a repo carries multiple ecosystems; the most specific
# language marker should appear first.
MARKER_TABLE: list[StackMarker] = [
    StackMarker(
        markers=["pyproject.toml", "setup.py", "setup.cfg"],
        profile=StackProfile(
            language="python",
            build=BuildConfig(command=""),
            test=TestConfig(
                command="pytest -v",
                file_globs=["**/test_*.py", "**/*_test.py", "**/tests/**/*.py"],
            ),
            quality=QualityConfig(commands=["ruff check", "ty check", "complexipy"]),
            run=RunConfig(command=""),
        ),
    ),
    StackMarker(
        markers=["Package.swift"],
        profile=StackProfile(
            language="swift",
            build=BuildConfig(command="swift build"),
            test=TestConfig(
                command="swift test",
                file_globs=["**/*Tests.swift", "**/Tests/**/*.swift"],
            ),
            quality=QualityConfig(commands=["swiftlint"]),
            run=RunConfig(command="swift run"),
        ),
    ),
    StackMarker(
        markers=["package.json"],
        profile=StackProfile(
            language="node",
            build=BuildConfig(command="npm run build"),
            test=TestConfig(
                command="npm test",
                file_globs=[
                    "**/*.test.ts",
                    "**/*.test.tsx",
                    "**/*.test.js",
                    "**/*.test.jsx",
                    "**/*.spec.ts",
                    "**/*.spec.tsx",
                    "**/*.spec.js",
                    "**/*.spec.jsx",
                ],
            ),
            quality=QualityConfig(commands=["eslint .", "tsc --noEmit"]),
            run=RunConfig(command="npm start"),
        ),
    ),
    StackMarker(
        markers=["Cargo.toml"],
        profile=StackProfile(
            language="rust",
            build=BuildConfig(command="cargo build"),
            test=TestConfig(command="cargo test", file_globs=["**/tests/**/*.rs"]),
            quality=QualityConfig(commands=["cargo clippy", "cargo check"]),
            run=RunConfig(command="cargo run"),
        ),
    ),
    StackMarker(
        markers=["go.mod"],
        profile=StackProfile(
            language="go",
            build=BuildConfig(command="go build ./..."),
            test=TestConfig(command="go test ./...", file_globs=["**/*_test.go"]),
            quality=QualityConfig(commands=["go vet ./...", "golangci-lint run"]),
            run=RunConfig(command="go run ."),
        ),
    ),
]


def _detect_from_markers(repo_root: Path) -> StackProfile:
    """Return the first profile whose marker file exists, else a blank one."""
    for marker in MARKER_TABLE:
        if any((repo_root / name).exists() for name in marker.markers):
            return marker.profile.model_copy(deep=True)
    return StackProfile()


def _load_override(repo_root: Path) -> dict[str, Any] | None:
    """Read ``.sahaidachny/stack.yaml`` if present, else return None."""
    stack_file = repo_root / STATE_DIRNAME / STACK_FILENAME
    if not stack_file.exists():
        return None
    try:
        data = yaml.safe_load(stack_file.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{stack_file} contains invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{stack_file} must contain a mapping, got {type(data).__name__}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively overlay ``override`` onto ``base`` (override wins)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_stack_profile(repo_root: Path | str = ".") -> StackProfile:
    """Resolve the stack profile for ``repo_root``.

    Auto-detects a base profile from marker files, then overlays any explicit
    ``.sahaidachny/stack.yaml`` on top so the file can fully or partially
    override the detected defaults.
    """
    root = Path(repo_root)
    detected = _detect_from_markers(root)
    override = _load_override(root)
    if override is None:
        return detected
    merged = _deep_merge(detected.model_dump(mode="json"), override)
    return StackProfile.model_validate(merged)
