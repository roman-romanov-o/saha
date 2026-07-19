"""Integration tests for language-agnostic stack profile resolution.

These exercise ``load_stack_profile`` end-to-end against a real filesystem
(``tmp_path``) — auto-detection per marker, partial/empty YAML override merge,
and test-file glob classification. No Docker needed: the resolution layer only
touches the filesystem and Pydantic models.
"""

import textwrap
from pathlib import Path

import pytest

from saha.config.stack import StackProfile, load_stack_profile, matches_any_glob

# (marker filename, expected language, expected test command) per ecosystem.
MARKER_CASES = [
    ("pyproject.toml", "python", "pytest -v"),
    ("Package.swift", "swift", "swift test"),
    ("package.json", "node", "npm test"),
    ("Cargo.toml", "rust", "cargo test"),
    ("go.mod", "go", "go test ./..."),
]


def _write_stack_yaml(repo_root: Path, body: str) -> None:
    """Drop a ``.sahaidachny/stack.yaml`` override into ``repo_root``."""
    state_dir = repo_root / ".sahaidachny"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "stack.yaml").write_text(textwrap.dedent(body))


class TestAutoDetection:
    """Marker files alone should resolve a sensible per-language profile."""

    @pytest.mark.parametrize("marker, language, test_command", MARKER_CASES)
    def test_detect_from_marker(self, tmp_path, marker, language, test_command):
        (tmp_path / marker).write_text("# marker\n")

        profile = load_stack_profile(tmp_path)

        assert profile.language == language
        assert profile.test.command == test_command
        assert profile.test.file_globs  # every detected stack declares globs

    def test_xcodeproj_glob_marker_detects_swift(self, tmp_path):
        # Pure Xcode apps have no Package.swift; the marker is a glob.
        (tmp_path / "MyApp.xcodeproj").mkdir()

        assert load_stack_profile(tmp_path).language == "swift"

    def test_no_marker_yields_neutral_skip_profile(self, tmp_path):
        profile = load_stack_profile(tmp_path)

        assert profile.language == "unknown"
        assert profile.build.command == ""
        assert profile.test.command == ""
        assert profile.quality.commands == []

    def test_first_marker_in_table_wins(self, tmp_path):
        # Python's marker precedes Node's in MARKER_TABLE.
        (tmp_path / "pyproject.toml").write_text("\n")
        (tmp_path / "package.json").write_text("{}\n")

        assert load_stack_profile(tmp_path).language == "python"


class TestYamlOverride:
    """An explicit stack.yaml overlays the detected base, field-by-field."""

    def test_partial_override_wins_per_field(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("\n")
        _write_stack_yaml(
            tmp_path,
            """
            test:
              command: pytest -x -q
            """,
        )

        profile = load_stack_profile(tmp_path)

        # Overridden field wins...
        assert profile.test.command == "pytest -x -q"
        # ...while untouched detected fields are preserved.
        assert profile.test.file_globs == [
            "**/test_*.py",
            "**/*_test.py",
            "**/tests/**/*.py",
        ]
        assert profile.quality.commands == ["ruff check", "ty check", "complexipy"]

    def test_override_can_change_language_and_run(self, tmp_path):
        (tmp_path / "Package.swift").write_text("\n")
        _write_stack_yaml(
            tmp_path,
            """
            language: swift-app
            run:
              command: xcodebuild -scheme App
            """,
        )

        profile = load_stack_profile(tmp_path)

        assert profile.language == "swift-app"
        assert profile.run.command == "xcodebuild -scheme App"
        # Detected build/test survive the partial override.
        assert profile.build.command == "swift build"
        assert profile.test.command == "swift test"

    def test_empty_command_means_skip_gate(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("\n")
        _write_stack_yaml(
            tmp_path,
            """
            test:
              command: ""
            quality:
              commands: []
            """,
        )

        profile = load_stack_profile(tmp_path)

        assert profile.test.command == ""
        assert profile.quality.commands == []

    def test_override_without_marker_defines_full_stack(self, tmp_path):
        _write_stack_yaml(
            tmp_path,
            """
            language: make
            build:
              command: make
            test:
              command: make test
              file_globs:
                - "**/*_test.c"
            """,
        )

        profile = load_stack_profile(tmp_path)

        assert profile.language == "make"
        assert profile.build.command == "make"
        assert profile.test.command == "make test"
        assert profile.test.file_globs == ["**/*_test.c"]

    def test_non_mapping_yaml_is_rejected(self, tmp_path):
        _write_stack_yaml(tmp_path, "- just\n- a\n- list\n")

        with pytest.raises(ValueError, match="must contain a mapping"):
            load_stack_profile(tmp_path)

    def test_malformed_yaml_gives_clear_error(self, tmp_path):
        # An unterminated flow mapping is a parse error, not a value error.
        _write_stack_yaml(tmp_path, "build: {command: 'swift build'\n")

        with pytest.raises(ValueError, match="invalid YAML"):
            load_stack_profile(tmp_path)


class TestTestFileClassification:
    """``StackProfile.is_test_file`` should follow declared globs."""

    @pytest.mark.parametrize(
        "language, path, expected",
        [
            ("python", "test_foo.py", True),
            ("python", "src/tests/unit/x.py", True),
            ("python", "saha/main.py", False),
            ("swift", "Sources/AppTests/FooTests.swift", True),
            ("swift", "Tests/AppTests/Bar.swift", True),
            ("swift", "Sources/App/Foo.swift", False),
            ("node", "src/util.test.ts", True),
            ("node", "src/util.ts", False),
            ("go", "pkg/thing_test.go", True),
            ("go", "pkg/thing.go", False),
        ],
    )
    def test_glob_classification(self, tmp_path, language, path, expected):
        marker = {
            "python": "pyproject.toml",
            "swift": "Package.swift",
            "node": "package.json",
            "go": "go.mod",
        }[language]
        (tmp_path / marker).write_text("\n")

        profile = load_stack_profile(tmp_path)

        assert profile.is_test_file(path) is expected

    def test_fallback_substring_when_no_globs(self):
        # A neutral profile has no globs; it falls back to a 'test' substring.
        profile = StackProfile()

        assert profile.is_test_file("some/test_thing.rb") is True
        assert profile.is_test_file("some/thing.rb") is False

    def test_globs_are_path_aware(self):
        # '*' must not cross '/' boundaries.
        assert matches_any_glob("a/b/test_x.py", ["**/test_*.py"]) is True
        assert matches_any_glob("a/test_x/y.py", ["**/test_*.py"]) is False

    def test_double_star_matches_zero_segments(self):
        # '**' matches zero or more path components, so '**/tests/**/*.py'
        # must still match a flat 'tests/x.py' with no intermediate dirs.
        assert matches_any_glob("tests/x.py", ["**/tests/**/*.py"]) is True
        assert matches_any_glob("a/b/tests/unit/x.py", ["**/tests/**/*.py"]) is True
