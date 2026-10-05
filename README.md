# refcheck

Extracts the reference lists from a directory of CS research paper PDFs,
merges/deduplicates references across all of them (flagging cases where
different papers cite the "same" work with conflicting metadata), then
cross-checks every unique reference against DBLP, Semantic Scholar, and
Crossref to surface references that look **hallucinated or fabricated**
(e.g. LLM-generated citations, or bad OCR/typos that turn into "no such
paper exists").

## Design priorities

This tool is tuned to **minimize false negatives** (a real hallucination
that gets waved through) even at the cost of **false positives** (a real
reference that gets flagged for review). Concretely:

- A reference is only marked `VERIFIED` when a strong, consistent match is
  found (high title similarity, and year/authors agreeing wherever both
  sides actually have that data).
- Anything short of that — moderate/conflicting matches, no match at all,
  or a source that errored before it could be checked — is pushed into a
  human-review queue (`MEDIUM_RISK`, `HIGH_RISK`, `UNVERIFIED`) rather than
  silently cleared.
- GROBID extraction runs in raw mode (`consolidateCitations=0`) so a
  fabricated reference's original (wrong) title/year/authors are preserved
  for verification, instead of GROBID quietly "fixing" it to the nearest
  real paper it can find.

**This tool is a triage aid, not a verdict.** Always have a human review
`output/human_review.md` before concluding a reference is actually
fabricated.

## Architecture

```
PDFs ──▶ GROBID (/api/processReferences, raw mode) ──▶ TEI XML
      ──▶ parsed into RawReference per citation
      ──▶ deduped/merged across all papers (fuzzy title + DOI/arXiv match)
      ──▶ each unique reference queried against DBLP + Semantic Scholar + Crossref
      ──▶ risk-classified (VERIFIED / MEDIUM_RISK / HIGH_RISK / UNVERIFIED)
      ──▶ reports written to output/
```

## Setup

Requires Docker (for GROBID) and Python 3.10+.

```bash
cd ref-check
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start GROBID (pulls the image on first run, ~ a few GB)
./scripts/start_grobid.sh
```

Put your PDFs in `./pdfs` (or point `--input` elsewhere).

## Usage

```bash
source .venv/bin/activate
python -m refcheck.cli run --input ./pdfs --output ./output
```

Or edit `config.yaml` and just run `python -m refcheck.cli run`.

Useful flags:

```bash
python -m refcheck.cli run \
  --input ./pdfs --output ./output \
  --sources dblp,semanticscholar,crossref \
  --workers 4 \
  --s2-api-key YOUR_KEY   # optional, raises Semantic Scholar's rate limit
```

Check GROBID connectivity without running the full pipeline:

```bash
python -m refcheck.cli check-grobid
```

## Output (`output/`)

- **`human_review.md`** — start here. Risk-sorted (`HIGH_RISK` →
  `UNVERIFIED` → `MEDIUM_RISK` → `VERIFIED`-with-conflicts), with the
  matched candidate (if any), reasons, and which papers cite it.
- **`human_review.csv`** — same data, spreadsheet-friendly for triage.
- **`references_report.json`** — full structured dump (every merged
  reference, every variant/citation instance, every candidate match, plus
  per-paper extraction issues).
- **`summary.md`** — headline counts.

### Risk levels

| Status | Meaning |
|---|---|
| `HIGH_RISK` | No sufficiently similar title found in any successfully-queried source. Most likely a fabrication (or a very obscure/new/non-indexed work — human judgment required). |
| `UNVERIFIED` | All sources errored (rate-limited/network) before a verdict could be reached. Re-run, don't treat as fine. |
| `MEDIUM_RISK` | A similar title exists somewhere, but year/authors/venue don't fully agree. Classic pattern for an LLM blending two real papers, or a straightforward mis-citation. |
| `VERIFIED` | Strong, consistent match. Still surfaced in the report if it has cross-paper metadata conflicts (see below). |

### Conflict detection

Independent of hallucination risk, when the *same* underlying work is cited
by multiple papers in your corpus with different year/venue/author metadata,
that's flagged as a conflict on the merged reference (e.g. one paper cites
it as 2017/NeurIPS, another as 2019/ICLR). This can indicate a copy-paste
citation error, a preprint-vs-camera-ready mismatch, or occasionally a
hallucinated variant — worth a human glance either way.

## Tuning

Thresholds live at the top of `refcheck/verify/aggregate.py`
(`STRONG_TITLE_SIM`, `MODERATE_TITLE_SIM`, `YEAR_TOLERANCE`,
`MIN_AUTHOR_OVERLAP_FOR_STRONG`) and `refcheck/dedupe.py`
(`TITLE_CLUSTER_THRESHOLD`, `VENUE_CONFLICT_THRESHOLD`,
`AUTHOR_CONFLICT_MIN_OVERLAP`). They're set conservatively by default; if
you find real references landing in `HIGH_RISK`/`MEDIUM_RISK` too often,
loosen `MODERATE_TITLE_SIM` or `MIN_AUTHOR_OVERLAP_FOR_STRONG` slightly —
but remember the design goal is to over-flag, not under-flag.

## Caching

- GROBID output is cached per-PDF (by content hash) under
  `.refcheck_cache/`, so re-running the pipeline on an unchanged file is
  free. Delete `.refcheck_cache/` (or pass `--no-cache`) to force
  re-extraction/re-verification.
- Verification API responses are cached per (source, normalized title), so
  re-runs and interrupted runs don't re-hit DBLP/Semantic Scholar/Crossref
  for references already looked up.

## Notes on the verification sources

- **DBLP** has no API key and excellent CS coverage, but its search
  relevance ranking is not pure text similarity — a very common title
  pattern (e.g. the many "X Is All You Need" papers) can push the actual
  target paper out of the top results. To compensate, when a reference has
  a year we also issue a `year:`-scoped query and merge its results in.
- **Semantic Scholar**'s unauthenticated tier shares a low rate-limit pool
  and 429s under load; pass `--s2-api-key` (or set `S2_API_KEY`) if you're
  processing many papers.
- **Crossref** covers a lot of non-CS-venue / older / book material DBLP
  doesn't index. `crossref_mailto` in `config.yaml` opts into their
  "polite pool" for better reliability (just a contact header, not sent
  anywhere sensitive) — change or blank it if you'd rather not.
- Google Scholar is intentionally not used: it has no official API, and
  scraping it violates its Terms of Service and gets blocked quickly.
