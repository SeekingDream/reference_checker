"""Minimal on-disk cache. Two uses in this project:

1. Caching raw GROBID TEI output per PDF (keyed by sha256 of file bytes) so
   re-running the pipeline doesn't re-hit the GROBID service for unchanged
   files.
2. Caching verification API responses (keyed by source + normalized query)
   so re-running (or resuming after an interruption) doesn't re-query
   DBLP/Semantic Scholar/Crossref for references we've already looked up.

No external dependency (no sqlite/diskcache) -- just one JSON file per key
under a cache directory, which is simple to inspect/clear by hand.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


class DiskCache:
    def __init__(self, cache_dir: str | Path):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.dir / f"{digest}.json"

    def get(self, key: str) -> Optional[Any]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)["value"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def set(self, key: str, value: Any) -> None:
        p = self._path(key)
        with p.open("w", encoding="utf-8") as f:
            json.dump({"key": key, "value": value}, f)

    def get_or_set(self, key: str, compute):
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute()
        self.set(key, value)
        return value


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
