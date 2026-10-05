"""Shared helpers for the verification-source clients."""
from __future__ import annotations

import threading
import time

from ..models import MatchCandidate, RawReference
from ..normalize import author_overlap, title_similarity


class RateLimiter:
    """A simple thread-safe minimum-interval limiter (not a token bucket --
    good enough for a sequential/pool-based pipeline hitting free public
    APIs politely)."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


def score_candidate(ref: RawReference, candidate: MatchCandidate) -> MatchCandidate:
    """Fill in title_similarity / author_overlap / year_diff on a candidate
    that already has raw fields populated."""
    candidate.title_similarity = title_similarity(ref.title, candidate.title)
    candidate.author_overlap = author_overlap(ref.authors, candidate.authors)
    if ref.year and candidate.year:
        candidate.year_diff = abs(ref.year - candidate.year)
    else:
        candidate.year_diff = None
    return candidate


class VerificationSourceError(RuntimeError):
    pass
