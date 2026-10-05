"""Data models shared across the extraction, dedupe, and verification stages."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class RiskLevel(str, Enum):
    """Human-review priority for a merged reference.

    Ordering (most to least urgent): HIGH_RISK, MEDIUM_RISK, VERIFIED.
    We deliberately bias toward pushing ambiguous cases up into HIGH/MEDIUM
    rather than auto-clearing them, since false positives are acceptable but
    false negatives (a missed hallucination) are not.
    """

    HIGH_RISK = "HIGH_RISK"          # not found (or only very weak match) in any source
    MEDIUM_RISK = "MEDIUM_RISK"      # a plausible match exists but metadata conflicts
    VERIFIED = "VERIFIED"            # strong, consistent match found
    UNVERIFIED = "UNVERIFIED"        # all sources errored out; verification incomplete


@dataclass
class RawReference:
    """A single reference entry as extracted from one paper's bibliography."""

    paper_id: str                 # source PDF filename (without extension)
    ref_index: int                # position within that paper's reference list
    raw_text: Optional[str] = None
    title: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MatchCandidate:
    """A candidate match returned by one verification source."""

    source: str                   # "dblp" | "semanticscholar" | "crossref"
    title: str
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    title_similarity: float = 0.0     # 0-100
    author_overlap: float = 0.0       # 0-1
    year_diff: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationResult:
    status: RiskLevel
    confidence: float                  # 0-1, rough confidence in `status`
    best_match: Optional[MatchCandidate] = None
    candidates: list[MatchCandidate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    sources_queried: list[str] = field(default_factory=list)
    sources_errored: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class FieldConflict:
    field: str
    values: dict[str, list[str]]   # value -> list of paper_ids reporting it

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MergedReference:
    """A cluster of RawReference instances (across papers) believed to be the
    same underlying work, plus any detected metadata conflicts."""

    key: str
    canonical_title: str
    canonical_authors: list[str] = field(default_factory=list)
    canonical_year: Optional[int] = None
    canonical_venue: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    variants: list[RawReference] = field(default_factory=list)
    cited_by_papers: list[str] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)
    verification: Optional[VerificationResult] = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "canonical_title": self.canonical_title,
            "canonical_authors": self.canonical_authors,
            "canonical_year": self.canonical_year,
            "canonical_venue": self.canonical_venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "cited_by_papers": self.cited_by_papers,
            "variants": [v.to_dict() for v in self.variants],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "verification": self.verification.to_dict() if self.verification else None,
        }
