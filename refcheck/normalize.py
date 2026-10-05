"""Text normalization + similarity helpers used for both dedupe and
verification matching."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_LEADING_ARTICLES = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


def normalize_title(title: str | None) -> str:
    """Lowercase, strip accents/punctuation/leading articles, collapse
    whitespace. Used both as a fuzzy-match input and as a blocking key."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().strip()
    t = _LEADING_ARTICLES.sub("", t)
    t = _NON_ALNUM.sub(" ", t)
    t = _WHITESPACE.sub(" ", t).strip()
    return t


def title_block_key(title: str | None, size: int = 8) -> str:
    """A coarse blocking key so we don't fuzzy-compare every reference
    against every other one (O(n^2)). References whose normalized titles
    don't share this prefix are never compared to each other.

    Trade-off: a typo/OCR error in the first `size` normalized characters
    could in theory prevent two mentions of the same paper from being
    clustered together. In practice GROBID's title extraction is clean
    enough that this is rare; increase `size` down or add a second pass if
    you observe missed duplicates in your corpus.
    """
    norm = normalize_title(title)
    compact = norm.replace(" ", "")
    return compact[:size]


def normalize_author_surname(name: str) -> str:
    """Extract a comparable surname token from a free-form author name."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.strip().lower()
    # Handle "Surname, Forename" vs "Forename Surname"
    if "," in n:
        surname = n.split(",")[0]
    else:
        parts = n.split()
        surname = parts[-1] if parts else n
    surname = _NON_ALNUM.sub("", surname)
    return surname


def author_surnames(authors: list[str]) -> set[str]:
    return {normalize_author_surname(a) for a in authors if a and normalize_author_surname(a)}


def title_similarity(a: str | None, b: str | None) -> float:
    """0-100 similarity between two titles using normalized token-sort ratio,
    which is robust to word reordering and minor punctuation differences."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return fuzz.token_sort_ratio(na, nb)


def author_overlap(a: list[str], b: list[str]) -> float:
    """Jaccard overlap of surname sets. Returns 0.0 if either side has no
    parseable authors (treated as "unknown", not "mismatch") -- callers
    should not penalize year/title confidence heavily on this alone."""
    sa, sb = author_surnames(a), author_surnames(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def venue_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(normalize_title(a), normalize_title(b))
