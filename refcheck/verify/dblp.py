"""DBLP publication search -- free, no API key, highly curated for CS.
https://dblp.org/faq/13501473.html
"""
from __future__ import annotations

import time

import requests

from ..models import MatchCandidate, RawReference
from .base import RateLimiter, VerificationSourceError, score_candidate

SOURCE = "dblp"
_BASE_URL = "https://dblp.org/search/publ/api"
_rate_limiter = RateLimiter(min_interval_seconds=1.0)


def _parse_authors(info: dict) -> list[str]:
    authors_block = info.get("authors")
    if not authors_block:
        return []
    author = authors_block.get("author")
    if author is None:
        return []
    if isinstance(author, dict):
        author = [author]
    names = []
    for a in author:
        if isinstance(a, dict):
            text = a.get("text")
        else:
            text = a
        if text:
            names.append(text)
    return names


def _raw_query(query: str, limit: int, timeout: int, max_retries: int = 2) -> list[dict]:
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        _rate_limiter.wait()
        try:
            resp = requests.get(
                _BASE_URL,
                params={"q": query, "format": "json", "h": limit},
                timeout=timeout,
                headers={"User-Agent": "refcheck/1.0 (reference hallucination checker)"},
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = VerificationSourceError(f"DBLP returned HTTP {resp.status_code}")
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("hits", {}).get("hit", []) or []
        except (requests.RequestException, ValueError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise VerificationSourceError(f"DBLP query failed after {max_retries + 1} attempts: {last_err}")


def search(ref: RawReference, limit: int = 5, timeout: int = 15) -> list[MatchCandidate]:
    """Queries DBLP for candidates matching `ref`.

    DBLP's relevance ranking is *not* a straight text-similarity ranking:
    for titles that match a common phrase pattern (e.g. the many papers
    titled "X Is All You Need"), the actual target paper can be absent even
    from the top 30 results of a plain title query. When the reference has
    a year, we additionally issue a `year:YYYY`-scoped query and merge its
    hits in -- this reliably surfaces the exact paper when the cited year is
    correct, without which we would have wrongly flagged plenty of real,
    well-known references as not-found. We still keep the plain-title
    results too, since a reference with a wrong/hallucinated year should not
    be rescued by a filter that assumes the year is right.
    """
    if not ref.title:
        return []

    hits = _raw_query(ref.title, limit, timeout)
    if ref.year:
        year_hits = _raw_query(f"{ref.title} year:{ref.year}", limit, timeout)
        seen_keys = {(h.get("info", {}).get("title"), h.get("info", {}).get("year")) for h in hits}
        for h in year_hits:
            key = (h.get("info", {}).get("title"), h.get("info", {}).get("year"))
            if key not in seen_keys:
                hits.append(h)
                seen_keys.add(key)

    candidates: list[MatchCandidate] = []
    for hit in hits:
        info = hit.get("info", {})
        title = info.get("title")
        if not title:
            continue
        year_raw = info.get("year")
        year = int(year_raw) if year_raw and str(year_raw).isdigit() else None
        cand = MatchCandidate(
            source=SOURCE,
            title=title,
            authors=_parse_authors(info),
            year=year,
            venue=info.get("venue"),
            doi=info.get("doi"),
            url=info.get("url"),
        )
        candidates.append(score_candidate(ref, cand))
    return candidates
