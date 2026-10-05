"""Combines candidates from DBLP / Semantic Scholar / Crossref into a single
conservative VerificationResult per merged reference.

Design goal (per project requirements): minimize false negatives (missed
hallucinations) even at the cost of false positives (flagging real
references for human review). Concretely:

  - VERIFIED requires a *strong, consistent* match: high title similarity,
    plus year and author agreement wherever those fields are actually
    present to compare. Anything short of that does NOT get cleared.
  - MEDIUM_RISK covers the classic "looks like a real paper but details are
    off" hallucination pattern (LLMs blending two real papers, wrong
    year/venue/authors on an otherwise-plausible title) as well as any
    match that's merely moderate-confidence.
  - HIGH_RISK is reserved for references with no meaningfully similar title
    in any successfully-queried source -- these are the most likely
    fabrications and should be reviewed first.
  - UNVERIFIED means every source errored (network/rate-limit) before a
    verdict could be reached; these must be retried or checked by hand,
    never silently treated as fine.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..cache import DiskCache
from ..models import MatchCandidate, MergedReference, RawReference, RiskLevel, VerificationResult
from . import crossref, dblp, semantic_scholar
from .base import VerificationSourceError

STRONG_TITLE_SIM = 90.0
MODERATE_TITLE_SIM = 78.0
YEAR_TOLERANCE = 1
MIN_AUTHOR_OVERLAP_FOR_STRONG = 0.2


@dataclass
class VerifyConfig:
    sources: tuple[str, ...] = ("dblp", "semanticscholar", "crossref")
    per_source_limit: int = 5
    s2_api_key: str | None = None
    crossref_mailto: str | None = None
    cache: DiskCache | None = None


_SOURCE_FUNCS = {
    "dblp": lambda ref, cfg: dblp.search(ref, limit=cfg.per_source_limit),
    "semanticscholar": lambda ref, cfg: semantic_scholar.search(
        ref, limit=cfg.per_source_limit, api_key=cfg.s2_api_key
    ),
    "crossref": lambda ref, cfg: crossref.search(
        ref, limit=cfg.per_source_limit, mailto=cfg.crossref_mailto
    ),
}


def _query_source(source: str, ref: RawReference, cfg: VerifyConfig) -> list[MatchCandidate]:
    fn = _SOURCE_FUNCS[source]

    def _do_query():
        cands = fn(ref, cfg)
        return [c.to_dict() for c in cands]

    if cfg.cache is not None:
        # Include year in the key: DBLP's query is year-aware (see dblp.py),
        # so two references sharing a title but asserting different years
        # must not collide on the same cache entry.
        cache_key = f"verify:{source}:{(ref.title or '').strip().lower()}:{ref.year or ''}"
        cached = cfg.cache.get(cache_key)
        if cached is not None:
            return [MatchCandidate(**c) for c in cached]
        result_dicts = _do_query()
        cfg.cache.set(cache_key, result_dicts)
        return [MatchCandidate(**c) for c in result_dicts]

    return fn(ref, cfg)


def _is_strong(ref: RawReference, cand: MatchCandidate) -> bool:
    if cand.title_similarity < STRONG_TITLE_SIM:
        return False
    if cand.year_diff is not None and cand.year_diff > YEAR_TOLERANCE:
        return False
    # Only require author agreement when both sides actually have parsed
    # authors to compare -- absence of authors on either side is "unknown",
    # not "mismatch", and must not block a VERIFIED verdict on its own.
    if ref.authors and cand.authors and cand.author_overlap < MIN_AUTHOR_OVERLAP_FOR_STRONG:
        return False
    return True


def verify_merged_reference(merged: MergedReference, cfg: VerifyConfig) -> VerificationResult:
    proxy_ref = RawReference(
        paper_id="__query__",
        ref_index=-1,
        title=merged.canonical_title,
        authors=merged.canonical_authors,
        year=merged.canonical_year,
        venue=merged.canonical_venue,
        doi=merged.doi,
        arxiv_id=merged.arxiv_id,
    )

    all_candidates: list[MatchCandidate] = []
    sources_queried: list[str] = []
    sources_errored: list[str] = []

    for source in cfg.sources:
        try:
            cands = _query_source(source, proxy_ref, cfg)
            sources_queried.append(source)
            all_candidates.extend(cands)
        except VerificationSourceError:
            sources_errored.append(source)

    if not sources_queried:
        return VerificationResult(
            status=RiskLevel.UNVERIFIED,
            confidence=0.0,
            candidates=[],
            reasons=["All verification sources errored; could not attempt a lookup."],
            sources_queried=sources_queried,
            sources_errored=sources_errored,
        )

    all_candidates.sort(key=lambda c: c.title_similarity, reverse=True)

    strong_matches = [c for c in all_candidates if _is_strong(proxy_ref, c)]
    if strong_matches:
        best = strong_matches[0]
        confidence = min(1.0, (best.title_similarity / 100.0) * (0.7 + 0.3 * best.author_overlap))
        reasons = [
            f"Strong match in {best.source}: title_sim={best.title_similarity:.0f}, "
            f"year_diff={best.year_diff}, author_overlap={best.author_overlap:.2f}"
        ]
        return VerificationResult(
            status=RiskLevel.VERIFIED,
            confidence=confidence,
            best_match=best,
            candidates=all_candidates[:10],
            reasons=reasons,
            sources_queried=sources_queried,
            sources_errored=sources_errored,
        )

    moderate_matches = [c for c in all_candidates if c.title_similarity >= MODERATE_TITLE_SIM]
    if moderate_matches:
        best = moderate_matches[0]
        reasons = [
            f"Moderate/conflicting match in {best.source}: title_sim={best.title_similarity:.0f}, "
            f"year_diff={best.year_diff}, author_overlap={best.author_overlap:.2f} "
            "-- similar title exists but metadata does not fully agree; verify by hand "
            "(this is the classic pattern for a blended/mis-cited hallucination)."
        ]
        confidence = best.title_similarity / 100.0 * 0.6
        return VerificationResult(
            status=RiskLevel.MEDIUM_RISK,
            confidence=confidence,
            best_match=best,
            candidates=all_candidates[:10],
            reasons=reasons,
            sources_queried=sources_queried,
            sources_errored=sources_errored,
        )

    best = all_candidates[0] if all_candidates else None
    reasons = [
        f"No sufficiently similar title found in {', '.join(sources_queried)} "
        f"(best title_sim={best.title_similarity:.0f} from {best.source})."
        if best
        else f"No results at all from {', '.join(sources_queried)}."
    ]
    if sources_errored:
        reasons.append(
            f"Note: {', '.join(sources_errored)} errored and were NOT checked -- "
            "consider re-running before treating this as confirmed."
        )
    return VerificationResult(
        status=RiskLevel.HIGH_RISK,
        confidence=0.8 if not sources_errored else 0.4,
        best_match=best,
        candidates=all_candidates[:10],
        reasons=reasons,
        sources_queried=sources_queried,
        sources_errored=sources_errored,
    )
