"""Thin client for the GROBID reference-extraction endpoint.

We deliberately call `/api/processReferences` (not the full-document
endpoint) since we only need the bibliography, and we deliberately run with
`consolidateCitations=0` (raw extraction, no external lookup/"correction").
GROBID's citation consolidation would query Crossref/biblio-glutton itself
and can silently rewrite a fabricated reference's fields to match the
nearest real paper it finds -- which is exactly the signal we need to
preserve for hallucination detection. Verification against DBLP/Semantic
Scholar/Crossref happens later, explicitly, over the raw extracted text.
"""
from __future__ import annotations

import time

import requests


class GrobidError(RuntimeError):
    pass


class GrobidClient:
    def __init__(self, base_url: str = "http://localhost:8070", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_alive(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/isalive", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def process_references(self, pdf_path: str, retries: int = 2) -> str:
        """Returns TEI XML (as text) containing the parsed bibliography."""
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with open(pdf_path, "rb") as f:
                    files = {"input": (pdf_path, f, "application/pdf")}
                    data = {
                        "consolidateCitations": "0",
                        "includeRawCitations": "1",
                    }
                    resp = requests.post(
                        f"{self.base_url}/api/processReferences",
                        files=files,
                        data=data,
                        timeout=self.timeout,
                    )
                if resp.status_code == 200:
                    return resp.text
                last_err = GrobidError(
                    f"GROBID returned HTTP {resp.status_code} for {pdf_path}: {resp.text[:300]}"
                )
            except requests.RequestException as e:
                last_err = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
        raise GrobidError(f"Failed to process {pdf_path} via GROBID: {last_err}")
