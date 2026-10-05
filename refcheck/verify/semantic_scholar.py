"""Semantic Scholar Graph API paper search -- free, broad coverage
including arXiv/preprints. https://api.semanticscholar.org/api-docs/graph

Unauthenticated requests share a low shared rate-limit pool and are prone to
429s; set S2_API_KEY (env var, passed in via config) to raise your limit.
"""
from __future__ import annotations

import os
import time

import requests

from ..models import MatchCandidate, RawReference
from .base import RateLimiter, VerificationSourceError, score_candidate

SOURCE = "semanticscholar"
_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_rate_limiter = RateLimiter(min_interval_seconds=1.1)


def search(
    ref: RawReference,
    limit: int = 5,
    timeout: int = 15,
    api_key: str | None = None,
    max_retries: int = 3,
) -> list[MatchCandidate]:
    if not ref.title:
        return []
    api_key = api_key or os.environ.get("S2_API_KEY")
    headers = {"User-Agent": "refcheck/1.0 (reference hallucination checker)"}
    if api_key:
        headers["x-api-key"] = api_key

    last_err: Exception | None = None
    for attempt in range(max_retries):
        _rate_limiter.wait()
        try:
            resp = requests.get(
                _BASE_URL,
                params={
                    "query": ref.title,
                    "limit": limit,
                    "fields": "title,authors,year,venue,externalIds",
                },
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code == 429:
                last_err = VerificationSourceError("Semantic Scholar rate-limited (429)")
                time.sleep(2 ** attempt * 2)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    else:
        raise VerificationSourceError(f"Semantic Scholar query failed: {last_err}")

    items = data.get("data", []) or []
    candidates: list[MatchCandidate] = []
    for item in items:
        title = item.get("title")
        if not title:
            continue
        authors = [a.get("name") for a in (item.get("authors") or []) if a.get("name")]
        external_ids = item.get("externalIds") or {}
        cand = MatchCandidate(
            source=SOURCE,
            title=title,
            authors=authors,
            year=item.get("year"),
            venue=item.get("venue"),
            doi=external_ids.get("DOI"),
            url=f"https://www.semanticscholar.org/paper/{item.get('paperId')}"
            if item.get("paperId")
            else None,
        )
        candidates.append(score_candidate(ref, cand))
    return candidates
