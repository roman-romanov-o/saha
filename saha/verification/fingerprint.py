"""Frozen-spec fingerprint for saha/v2 tasks.

The LikeC4 model is frozen once execution starts: the loop records this
fingerprint at kickoff and refuses DoD completion if it changed. Any edit
to model/*.c4 mid-execution is a spec violation, not progress.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def compute_model_fingerprint(task_path: Path) -> str | None:
    """sha256 over sorted model/*.c4 (name + bytes); None when no model."""
    folder = task_path / "model"
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.c4"))
    if not files:
        return None
    digest = hashlib.sha256()
    for path in files:
        try:
            content = path.read_bytes()
        except OSError as exc:
            logger.warning("Fingerprint skipping unreadable %s: %s", path, exc)
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(content)
        digest.update(b"\x00")
    return digest.hexdigest()
