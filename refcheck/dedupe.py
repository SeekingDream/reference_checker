"""Clusters RawReference instances (drawn from many papers) that refer to
the same underlying work, and flags cases where those instances disagree
on year/venue/authors (e.g. two papers cite the "same" work with different
years -- worth a human's attention, since it can indicate a mis-citation on
one side or, occasionally, a hallucinated variant of a real title).
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .models import FieldConflict, MergedReference, RawReference
from .normalize import (
    author_overlap,
    normalize_title,
    title_block_key,
    title_similarity,
    venue_similarity,
)

TITLE_CLUSTER_THRESHOLD = 92.0   # min title_similarity to consider "same work"
VENUE_CONFLICT_THRESHOLD = 55.0  # below this, distinct venues are flagged as conflicting
AUTHOR_CONFLICT_MIN_OVERLAP = 0.3


class _DSU:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _cluster_indices(refs: list[RawReference]) -> list[list[int]]:
    dsu = _DSU(len(refs))

    # Blocking pass: only fuzzy-compare references whose normalized titles
    # share a prefix, to keep this sub-quadratic on large corpora.
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(refs):
        blocks[title_block_key(r.title)].append(i)

    for idxs in blocks.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                if title_similarity(refs[i].title, refs[j].title) >= TITLE_CLUSTER_THRESHOLD:
                    dsu.union(i, j)

    # Identifier pass: merge clusters that share a DOI or arXiv id even if
    # blocking (or OCR/title-parsing noise) kept them apart above.
    doi_first: dict[str, int] = {}
    arxiv_first: dict[str, int] = {}
    for i, r in enumerate(refs):
        if r.doi:
            key = r.doi.strip().lower()
            if key in doi_first:
                dsu.union(i, doi_first[key])
            else:
                doi_first[key] = i
        if r.arxiv_id:
            key = r.arxiv_id.strip().lower()
            if key in arxiv_first:
                dsu.union(i, arxiv_first[key])
            else:
                arxiv_first[key] = i

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(refs)):
        groups[dsu.find(i)].append(i)
    return list(groups.values())


def _pick_canonical(values: list[str | None]) -> str | None:
    non_null = [v for v in values if v]
    if not non_null:
        return None
    counts = Counter(non_null)
    # Prefer the most common value; break ties by longest (usually more complete)
    best_count = max(counts.values())
    tied = [v for v, c in counts.items() if c == best_count]
    return max(tied, key=len)


def _pick_canonical_year(values: list[int | None]) -> int | None:
    non_null = [v for v in values if v]
    if not non_null:
        return None
    return Counter(non_null).most_common(1)[0][0]


def _pick_canonical_authors(refs: list[RawReference]) -> list[str]:
    with_authors = [r.authors for r in refs if r.authors]
    if not with_authors:
        return []
    return max(with_authors, key=len)


def _detect_conflicts(refs: list[RawReference]) -> list[FieldConflict]:
    conflicts: list[FieldConflict] = []

    years = [r.year for r in refs if r.year]
    distinct_years = set(years)
    if len(distinct_years) > 1 and max(distinct_years) - min(distinct_years) > 1:
        values: dict[str, list[str]] = defaultdict(list)
        for r in refs:
            if r.year:
                values[str(r.year)].append(r.paper_id)
        conflicts.append(FieldConflict(field="year", values=dict(values)))

    venues = [r.venue for r in refs if r.venue]
    distinct_venues = list({normalize_title(v): v for v in venues}.values())
    if len(distinct_venues) > 1:
        # Use the *minimum* pairwise similarity, not the max: if any two
        # venues among the variants disagree, that's a conflict worth
        # surfacing, even if some other pair of variants happens to agree
        # (e.g. NeurIPS/NIPS/ICLR -- NeurIPS~NIPS is a fine match, but ICLR
        # is a real outlier that a max-based check would hide).
        min_pair_sim = min(
            venue_similarity(distinct_venues[a], distinct_venues[b])
            for a in range(len(distinct_venues))
            for b in range(a + 1, len(distinct_venues))
        )
        if min_pair_sim < VENUE_CONFLICT_THRESHOLD:
            values = defaultdict(list)
            for r in refs:
                if r.venue:
                    values[r.venue].append(r.paper_id)
            conflicts.append(FieldConflict(field="venue", values=dict(values)))

    author_lists = [r.authors for r in refs if r.authors]
    if len(author_lists) > 1:
        min_overlap = min(
            author_overlap(author_lists[a], author_lists[b])
            for a in range(len(author_lists))
            for b in range(a + 1, len(author_lists))
        )
        if min_overlap < AUTHOR_CONFLICT_MIN_OVERLAP:
            values = defaultdict(list)
            for r in refs:
                if r.authors:
                    values[", ".join(r.authors)].append(r.paper_id)
            if len(values) > 1:
                conflicts.append(FieldConflict(field="authors", values=dict(values)))

    return conflicts


def merge_references(all_refs: list[RawReference]) -> list[MergedReference]:
    """Cluster raw references across all papers and build MergedReference
    entries with canonical fields + conflict annotations."""
    clusters = _cluster_indices(all_refs)
    merged: list[MergedReference] = []

    for cluster_idx, idxs in enumerate(clusters):
        refs = [all_refs[i] for i in idxs]
        titles = [r.title for r in refs]
        canonical_title = _pick_canonical(titles) or ""
        canonical_authors = _pick_canonical_authors(refs)
        canonical_year = _pick_canonical_year([r.year for r in refs])
        canonical_venue = _pick_canonical([r.venue for r in refs])
        doi = _pick_canonical([r.doi for r in refs])
        arxiv_id = _pick_canonical([r.arxiv_id for r in refs])
        cited_by = sorted({r.paper_id for r in refs})
        conflicts = _detect_conflicts(refs)

        key = f"ref-{cluster_idx:06d}"
        merged.append(
            MergedReference(
                key=key,
                canonical_title=canonical_title,
                canonical_authors=canonical_authors,
                canonical_year=canonical_year,
                canonical_venue=canonical_venue,
                doi=doi,
                arxiv_id=arxiv_id,
                variants=refs,
                cited_by_papers=cited_by,
                conflicts=conflicts,
            )
        )
    return merged
