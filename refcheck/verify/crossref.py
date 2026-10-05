"""Crossref works search -- free, no key, huge DOI-registered metadata
coverage (journals, conferences, books). Passing `mailto` opts into
Crossref's "polite pool" for better reliability/rate limits.
https://api.crossref.org/swagger-ui/index.html
"""
from __future__ import annotations

import time

import requests

from ..models import MatchCandidate, RawReference
from .base import RateLimiter, VerificationSourceError, score_candidate

SOURCE = "crossref"
_BASE_URL = "https://api.crossref.org/works"
_rate_limiter = RateLimiter(min_interval_seconds=1.0)


def _parse_authors(item: dict) -> list[str]:
    names = []
    for a in item.get("author", []) or []:
        given = a.get("given", "")
        family = a.get("family", "")
        full = f"{given} {family}".strip()
        if full:
            names.append(full)
    return names


def _parse_year(item: dict) -> int | None:
    for key in ("published-print", "published-online", "published", "issued"):
        block = item.get(key)
        if block and block.get("date-parts"):
            parts = block["date-parts"][0]
            if parts and parts[0]:
                return int(parts[0])
    return None


def search(
    ref: RawReference,
    limit: int = 5,
    timeout: int = 15,
    mailto: str | None = None,
    max_retries: int = 2,
) -> list[MatchCandidate]:
    if not ref.title:
        return []
    params = {"query.bibliographic": ref.title, "rows": limit}
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": f"refcheck/1.0 (mailto:{mailto or 'unset'})"}

    last_err: Exception | None = None
    data = None
    for attempt in range(max_retries + 1):
        _rate_limiter.wait()
        try:
            resp = requests.get(_BASE_URL, params=params, timeout=timeout, headers=headers)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = VerificationSourceError(f"Crossref returned HTTP {resp.status_code}")
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    if data is None:
        raise VerificationSourceError(f"Crossref query failed after {max_retries + 1} attempts: {last_err}")

    items = data.get("message", {}).get("items", []) or []
    candidates: list[MatchCandidate] = []
    for item in items:
        titles = item.get("title") or []
        if not titles:
            continue
        title = titles[0]
        container = item.get("container-title") or []
        cand = MatchCandidate(
            source=SOURCE,
            title=title,
            authors=_parse_authors(item),
            year=_parse_year(item),
            venue=container[0] if container else None,
            doi=item.get("DOI"),
            url=item.get("URL"),
        )
        candidates.append(score_candidate(ref, cand))
    return candidates
