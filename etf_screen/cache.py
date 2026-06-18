"""Tiny per-day disk cache so a full ~100-name run is resumable and gentle on
rate limits.

Cached payloads are plain JSON under ``.cache/<provider>/<YYYY-MM-DD>/<key>.json``
(the ``.cache`` dir is git-ignored). A new day starts a fresh namespace, which is
the desired behavior for daily fundamentals.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

CACHE_ROOT = Path(".cache")


class DiskCache:
    """JSON file cache namespaced by provider and the current date."""

    def __init__(self, provider: str, enabled: bool = True, refresh: bool = False):
        self.enabled = enabled
        self.refresh = refresh
        self.dir = CACHE_ROOT / provider / date.today().isoformat()

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("\\", "_")
        return self.dir / f"{safe}.json"

    def get(self, key: str) -> Any | None:
        """Return the cached value for ``key``, or None on miss/disabled/refresh."""
        if not self.enabled or self.refresh:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None  # treat a corrupt entry as a miss

    def set(self, key: str, value: Any) -> None:
        """Persist ``value`` for ``key`` (no-op when caching is disabled)."""
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps(value))
