"""End-to-end orchestration: PDF -> GROBID -> parsed references -> dedupe ->
verification -> report-ready data structures."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

from .cache import DiskCache, sha256_file
from .dedupe import merge_references
from .grobid_client import GrobidClient, GrobidError
from .models import MergedReference, RawReference
from .tei_parser import parse_tei_references
from .verify.aggregate import VerifyConfig, verify_merged_reference

log = logging.getLogger("refcheck")


@dataclass
class PipelineConfig:
    input_dir: str
    output_dir: str
    grobid_url: str = "http://localhost:8070"
    cache_dir: str = ".refcheck_cache"
    verify_sources: tuple[str, ...] = ("dblp", "semanticscholar", "crossref")
    s2_api_key: str | None = None
    crossref_mailto: str | None = None
    verify_workers: int = 4
    per_source_limit: int = 5
    use_grobid_cache: bool = True


@dataclass
class PipelineResult:
    merged_references: list[MergedReference] = field(default_factory=list)
    extraction_issues: list[dict] = field(default_factory=list)  # {paper_id, issue}
    paper_ref_counts: dict[str, int] = field(default_factory=dict)


def discover_pdfs(input_dir: str) -> list[Path]:
    return sorted(Path(input_dir).glob("*.pdf"))


def extract_all_references(cfg: PipelineConfig, cache: DiskCache) -> PipelineResult:
    grobid = GrobidClient(base_url=cfg.grobid_url)
    if not grobid.is_alive():
        raise GrobidError(
            f"GROBID is not reachable at {cfg.grobid_url}. "
            "Start it with `scripts/start_grobid.sh` (requires Docker running) and retry."
        )

    pdfs = discover_pdfs(cfg.input_dir)
    if not pdfs:
        raise FileNotFoundError(f"No PDF files found in {cfg.input_dir}")

    all_refs: list[RawReference] = []
    result = PipelineResult()

    for pdf_path in tqdm(pdfs, desc="Extracting references (GROBID)"):
        paper_id = pdf_path.stem
        try:
            file_hash = sha256_file(pdf_path)
            cache_key = f"grobid_tei:{file_hash}"
            tei_xml = cache.get(cache_key) if cfg.use_grobid_cache else None
            if tei_xml is None:
                tei_xml = grobid.process_references(str(pdf_path))
                if cfg.use_grobid_cache:
                    cache.set(cache_key, tei_xml)
        except GrobidError as e:
            log.warning("GROBID failed on %s: %s", pdf_path.name, e)
            result.extraction_issues.append({"paper_id": paper_id, "issue": str(e)})
            continue

        refs = parse_tei_references(tei_xml, paper_id)
        if not refs:
            result.extraction_issues.append(
                {"paper_id": paper_id, "issue": "No references extracted (empty or unparsed bibliography)"}
            )
        result.paper_ref_counts[paper_id] = len(refs)
        all_refs.extend(refs)

    result.merged_references = merge_references(all_refs)
    return result


def verify_all(
    merged_refs: list[MergedReference], cfg: PipelineConfig, cache: DiskCache
) -> None:
    """Verifies each merged reference in place (sets .verification)."""
    verify_cfg = VerifyConfig(
        sources=cfg.verify_sources,
        per_source_limit=cfg.per_source_limit,
        s2_api_key=cfg.s2_api_key,
        crossref_mailto=cfg.crossref_mailto,
        cache=cache,
    )

    def _verify_one(m: MergedReference) -> None:
        m.verification = verify_merged_reference(m, verify_cfg)

    with ThreadPoolExecutor(max_workers=cfg.verify_workers) as pool:
        futures = {pool.submit(_verify_one, m): m for m in merged_refs}
        for _ in tqdm(
            as_completed(futures), total=len(futures), desc="Verifying references"
        ):
            pass


def run_pipeline(cfg: PipelineConfig) -> PipelineResult:
    cache = DiskCache(cfg.cache_dir)
    result = extract_all_references(cfg, cache)
    verify_all(result.merged_references, cfg, cache)
    return result
