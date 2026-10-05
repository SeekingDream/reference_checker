from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from .grobid_client import GrobidClient
from .pipeline import PipelineConfig, run_pipeline
from .report import write_all_reports


def _load_yaml_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_pipeline_config(args: argparse.Namespace) -> PipelineConfig:
    file_cfg = _load_yaml_config(args.config)

    def pick(cli_val, key, default):
        if cli_val is not None:
            return cli_val
        return file_cfg.get(key, default)

    verify_sources = pick(args.sources, "verify_sources", ["dblp", "semanticscholar", "crossref"])
    if isinstance(verify_sources, str):
        verify_sources = [s.strip() for s in verify_sources.split(",") if s.strip()]

    return PipelineConfig(
        input_dir=pick(args.input, "input_dir", "./pdfs"),
        output_dir=pick(args.output, "output_dir", "./output"),
        grobid_url=pick(args.grobid_url, "grobid_url", "http://localhost:8070"),
        cache_dir=pick(args.cache_dir, "cache_dir", "./.refcheck_cache"),
        verify_sources=tuple(verify_sources),
        s2_api_key=pick(args.s2_api_key, "s2_api_key", "") or None,
        crossref_mailto=pick(args.crossref_mailto, "crossref_mailto", "") or None,
        verify_workers=pick(args.workers, "verify_workers", 4),
        per_source_limit=pick(args.per_source_limit, "per_source_limit", 5),
        use_grobid_cache=not args.no_cache,
    )


def cmd_run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = _build_pipeline_config(args)

    grobid = GrobidClient(base_url=cfg.grobid_url)
    if not grobid.is_alive():
        print(
            f"ERROR: GROBID is not reachable at {cfg.grobid_url}.\n"
            "Start it with: ./scripts/start_grobid.sh  (requires Docker running)",
            file=sys.stderr,
        )
        return 1

    result = run_pipeline(cfg)
    write_all_reports(result, cfg.output_dir)

    def _count(status: str, error_affected: bool | None = None) -> int:
        n = 0
        for m in result.merged_references:
            if not m.verification or m.verification.status.value != status:
                continue
            if error_affected is not None and bool(m.verification.sources_errored) != error_affected:
                continue
            n += 1
        return n

    n_high = _count("HIGH_RISK")
    n_high_errored = _count("HIGH_RISK", error_affected=True)
    n_unverified = _count("UNVERIFIED")

    print(f"\nDone. {len(result.merged_references)} unique references checked.")
    print(f"  HIGH_RISK (likely fabricated): {n_high}" + (
        f"  [{n_high_errored} of these had a source error -- re-run before trusting]"
        if n_high_errored else ""
    ))
    print(f"  UNVERIFIED (all sources errored, retry needed): {n_unverified}")
    print(f"Reports written to {cfg.output_dir}/ (start with human_review.md)")
    return 0


def cmd_check_grobid(args: argparse.Namespace) -> int:
    file_cfg = _load_yaml_config(args.config)
    url = args.grobid_url or file_cfg.get("grobid_url", "http://localhost:8070")
    grobid = GrobidClient(base_url=url)
    if grobid.is_alive():
        print(f"GROBID is reachable at {url}")
        return 0
    print(f"GROBID is NOT reachable at {url}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="refcheck", description="Extract and verify references from CS papers")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: ./config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full extraction + verification pipeline")
    p_run.add_argument("--input", help="Directory of input PDF files")
    p_run.add_argument("--output", help="Directory to write reports to")
    p_run.add_argument("--grobid-url", dest="grobid_url", help="GROBID base URL")
    p_run.add_argument("--cache-dir", dest="cache_dir", help="Disk cache directory")
    p_run.add_argument("--sources", help="Comma-separated verification sources (dblp,semanticscholar,crossref)")
    p_run.add_argument("--s2-api-key", dest="s2_api_key", help="Semantic Scholar API key")
    p_run.add_argument("--crossref-mailto", dest="crossref_mailto", help="Contact email for Crossref polite pool")
    p_run.add_argument("--workers", type=int, help="Parallel verification workers")
    p_run.add_argument("--per-source-limit", dest="per_source_limit", type=int, help="Results to fetch per source per query")
    p_run.add_argument("--no-cache", action="store_true", help="Disable GROBID/verification result caching")
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser("check-grobid", help="Check whether the GROBID service is reachable")
    p_check.add_argument("--grobid-url", dest="grobid_url", help="GROBID base URL")
    p_check.set_defaults(func=cmd_check_grobid)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
