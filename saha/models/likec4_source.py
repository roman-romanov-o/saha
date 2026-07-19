"""Text-level helpers over a saha/v2 task's LikeC4 model sources.

The ``model/*.c4`` files are the frozen spec. Python tooling never parses
LikeC4 semantically (``likec4 validate`` owns well-formedness); it only
needs two text-level operations:

- which view ids exist (for cross-reference checks), and
- the source block of a single view (to embed a story/test flow in an
  agent prompt without shipping the entire model).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_VIEW_DECL = re.compile(r"^\s*(?:dynamic\s+)?view\s+(?P<id>[A-Za-z][\w-]*)\b", re.MULTILINE)


def model_dir(task_path: Path) -> Path:
    return task_path / "model"


def read_model_sources(task_path: Path) -> dict[str, str]:
    """Return {filename: content} for every model/*.c4, sorted by name."""
    folder = model_dir(task_path)
    if not folder.is_dir():
        return {}
    sources: dict[str, str] = {}
    for path in sorted(folder.glob("*.c4")):
        try:
            sources[path.name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to read %s: %s", path, exc)
    return sources


def view_ids(sources: dict[str, str]) -> set[str]:
    """All view ids declared across the given sources."""
    ids: set[str] = set()
    for text in sources.values():
        ids.update(match.group("id") for match in _VIEW_DECL.finditer(text))
    return ids


def extract_view_block(source: str, view_id: str) -> str | None:
    """Slice the full ``[dynamic] view <id> { … }`` block out of one source.

    Brace matching skips LikeC4 string literals (``'…'`` and ``'''…'''``)
    so markdown braces inside notes/descriptions don't unbalance the scan.
    """
    decl = re.compile(
        r"(?:dynamic\s+)?view\s+" + re.escape(view_id) + r"\b[^\n{]*\{",
    )
    match = decl.search(source)
    if not match:
        return None
    end = _matching_brace_end(source, match.end() - 1)
    if end is None:
        logger.warning("Unbalanced braces while extracting view %s", view_id)
        return None
    return source[match.start() : end + 1]


def _matching_brace_end(text: str, open_index: int) -> int | None:
    """Index of the ``}`` closing the ``{`` at open_index, string-aware."""
    depth = 0
    i = open_index
    while i < len(text):
        if text.startswith("'''", i):
            closing = text.find("'''", i + 3)
            if closing == -1:
                return None
            i = closing + 3
            continue
        char = text[i]
        if char == "'":
            closing = text.find("'", i + 1)
            if closing == -1:
                return None
            i = closing + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None
