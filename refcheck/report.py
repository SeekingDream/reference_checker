"""Writes the pipeline's output: full JSON dump, a prioritized human-review
markdown/CSV, and a plain-text summary."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import MergedReference, RiskLevel
from .pipeline import PipelineResult

# Review priority: most urgent first. UNVERIFIED comes right after HIGH_RISK
# since "we don't know" must never be silently treated as "it's fine".
_RISK_ORDER = {
    RiskLevel.HIGH_RISK: 0,
    RiskLevel.UNVERIFIED: 1,
    RiskLevel.MEDIUM_RISK: 2,
    RiskLevel.VERIFIED: 3,
}


def _source_error_affected(m: MergedReference) -> bool:
    """True if this reference's verdict was reached with one or more
    sources unreachable -- i.e. the verdict is based on partial evidence
    and deserves a re-run before being trusted, distinct from a verdict
    where every configured source was actually checked."""
    return bool(m.verification and m.verification.sources_errored)


def _sort_key(m: MergedReference):
    risk = m.verification.status if m.verification else RiskLevel.UNVERIFIED
    has_conflict = 0 if m.conflicts else 1
    error_affected = 1 if _source_error_affected(m) else 0
    conf = m.verification.confidence if m.verification else 0.0
    # Within a risk tier, surface clean (all-sources-checked) verdicts
    # before ones weakened by a source error -- the former are the more
    # actionable/confident findings.
    return (_RISK_ORDER.get(risk, 9), error_affected, has_conflict, -conf)


def write_json(result: PipelineResult, out_dir: Path) -> None:
    data = {
        "merged_references": [m.to_dict() for m in result.merged_references],
        "extraction_issues": result.extraction_issues,
        "paper_ref_counts": result.paper_ref_counts,
    }
    with (out_dir / "references_report.json").open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_csv(result: PipelineResult, out_dir: Path) -> None:
    rows = sorted(result.merged_references, key=_sort_key)
    with (out_dir / "human_review.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "risk",
                "confidence",
                "key",
                "canonical_title",
                "canonical_year",
                "canonical_venue",
                "cited_by_papers",
                "num_variants",
                "has_conflict",
                "conflict_fields",
                "best_match_source",
                "best_match_title",
                "title_similarity",
                "year_diff",
                "author_overlap",
                "reasons",
            ]
        )
        for m in rows:
            v = m.verification
            best = v.best_match if v else None
            writer.writerow(
                [
                    v.status.value if v else "UNVERIFIED",
                    f"{v.confidence:.2f}" if v else "",
                    m.key,
                    m.canonical_title,
                    m.canonical_year or "",
                    m.canonical_venue or "",
                    "; ".join(m.cited_by_papers),
                    len(m.variants),
                    "yes" if m.conflicts else "no",
                    "; ".join(c.field for c in m.conflicts),
                    best.source if best else "",
                    best.title if best else "",
                    f"{best.title_similarity:.0f}" if best else "",
                    best.year_diff if best and best.year_diff is not None else "",
                    f"{best.author_overlap:.2f}" if best else "",
                    " | ".join(v.reasons) if v else "",
                ]
            )


def _fmt_ref_md(m: MergedReference) -> list[str]:
    v = m.verification
    lines = [f"### [{m.key}] {m.canonical_title or '(no title extracted)'}"]
    meta = []
    if m.canonical_authors:
        meta.append(", ".join(m.canonical_authors[:6]) + (" et al." if len(m.canonical_authors) > 6 else ""))
    if m.canonical_year:
        meta.append(str(m.canonical_year))
    if m.canonical_venue:
        meta.append(m.canonical_venue)
    if meta:
        lines.append("- " + " · ".join(meta))
    lines.append(f"- Cited by: {', '.join(m.cited_by_papers)} ({len(m.variants)} instance(s))")
    if v:
        lines.append(f"- **Status: {v.status.value}** (confidence {v.confidence:.2f})")
        for r in v.reasons:
            lines.append(f"  - {r}")
        if v.best_match:
            bm = v.best_match
            lines.append(
                f"  - Best match ({bm.source}): \"{bm.title}\" "
                f"[{', '.join(bm.authors[:4])}{' et al.' if len(bm.authors) > 4 else ''}] "
                f"{bm.year or '?'} — {bm.venue or 'n/a'}"
                + (f" — {bm.url}" if bm.url else "")
            )
        if v.sources_errored:
            lines.append(f"  - ⚠ sources not checked (errored): {', '.join(v.sources_errored)}")
    if m.conflicts:
        lines.append("- **Conflicting metadata across citing papers:**")
        for c in m.conflicts:
            lines.append(f"  - `{c.field}` disagrees:")
            for val, papers in c.values.items():
                lines.append(f"    - `{val}` — cited this way in: {', '.join(papers)}")
    if m.variants and m.variants[0].raw_text:
        lines.append(f"- Raw citation text (1st instance): {m.variants[0].raw_text}")
    lines.append("")
    return lines


def write_markdown(result: PipelineResult, out_dir: Path) -> None:
    rows = sorted(result.merged_references, key=_sort_key)
    by_status: dict[str, list[MergedReference]] = {}
    for m in rows:
        status = m.verification.status if m.verification else RiskLevel.UNVERIFIED
        by_status.setdefault(status.value, []).append(m)

    lines = ["# Reference Verification — Human Review\n"]
    lines.append(
        "Sorted by risk (most urgent first). `HIGH_RISK` = no similar title found in any "
        "source (most likely fabricated). `UNVERIFIED` = could not complete the check "
        "(retry). `MEDIUM_RISK` = a similar title exists but year/authors/venue don't "
        "fully agree (classic hallucination-blend pattern, but can also be a citation "
        "error). `VERIFIED` = strong consistent match; still listed here if it has "
        "cross-paper metadata conflicts.\n"
    )

    counts = {k: len(v) for k, v in by_status.items()}
    lines.append("## Summary\n")
    for status in ("HIGH_RISK", "UNVERIFIED", "MEDIUM_RISK", "VERIFIED"):
        lines.append(f"- {status}: {counts.get(status, 0)}")
    lines.append(f"- Total unique references: {len(rows)}")
    lines.append(f"- References with cross-paper conflicts: {sum(1 for m in rows if m.conflicts)}")
    lines.append("")

    for status in ("HIGH_RISK", "UNVERIFIED", "MEDIUM_RISK", "VERIFIED"):
        group = by_status.get(status, [])
        if status == "VERIFIED":
            group = [m for m in group if m.conflicts]
            header = "## VERIFIED but with cross-paper conflicts"
        else:
            header = f"## {status} ({len(group)})"
        if not group:
            continue
        lines.append(header + "\n")
        if status in ("HIGH_RISK", "MEDIUM_RISK"):
            clean = [m for m in group if not _source_error_affected(m)]
            affected = [m for m in group if _source_error_affected(m)]
            if clean:
                lines.append(f"#### Checked against all sources ({len(clean)})\n")
                for m in clean:
                    lines.extend(_fmt_ref_md(m))
            if affected:
                lines.append(
                    f"#### ⚠ One or more sources errored -- re-run before trusting ({len(affected)})\n"
                )
                for m in affected:
                    lines.extend(_fmt_ref_md(m))
        else:
            for m in group:
                lines.extend(_fmt_ref_md(m))

    if result.extraction_issues:
        lines.append("## Extraction issues (papers GROBID had trouble with)\n")
        for issue in result.extraction_issues:
            lines.append(f"- **{issue['paper_id']}**: {issue['issue']}")
        lines.append("")

    with (out_dir / "human_review.md").open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_summary(result: PipelineResult, out_dir: Path) -> None:
    counts: dict[str, int] = {}
    error_affected_counts: dict[str, int] = {}
    for m in result.merged_references:
        status = m.verification.status.value if m.verification else "UNVERIFIED"
        counts[status] = counts.get(status, 0) + 1
        if _source_error_affected(m):
            error_affected_counts[status] = error_affected_counts.get(status, 0) + 1

    lines = ["# refcheck summary\n"]
    lines.append(f"Papers processed: {len(result.paper_ref_counts)}")
    lines.append(f"Raw reference instances: {sum(result.paper_ref_counts.values())}")
    lines.append(f"Unique (deduped) references: {len(result.merged_references)}")
    lines.append("")
    for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        affected = error_affected_counts.get(status, 0)
        note = f" ({affected} affected by a source error -- re-run recommended)" if affected else ""
        lines.append(f"- {status}: {n}{note}")
    lines.append("")
    lines.append(f"Papers with extraction issues: {len(result.extraction_issues)}")
    lines.append("")
    lines.append("See human_review.md (priority-sorted narrative), human_review.csv "
                  "(spreadsheet-friendly), and references_report.json (full data).")

    with (out_dir / "summary.md").open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_all_reports(result: PipelineResult, output_dir: str) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(result, out_dir)
    write_csv(result, out_dir)
    write_markdown(result, out_dir)
    write_summary(result, out_dir)
