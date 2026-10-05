"""Parses the TEI XML returned by GROBID's /api/processReferences into
RawReference objects."""
from __future__ import annotations

import re

from lxml import etree

from .models import RawReference

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}

_YEAR_RE = re.compile(r"(1[5-9]\d{2}|20\d{2})")
_ARXIV_RE = re.compile(r"arxiv[:\s]*([0-9]{4}\.[0-9]{4,5}(v\d+)?)", re.IGNORECASE)


def _text(el) -> str | None:
    if el is None:
        return None
    txt = "".join(el.itertext()).strip()
    return txt or None


def _extract_authors(bibl_struct) -> list[str]:
    names = []
    # Prefer analytic-level authors (the actual paper authors); fall back to
    # monograph-level (e.g. book editors) if no analytic authors exist.
    for scope in ("analytic", "monogr"):
        persons = bibl_struct.xpath(f"tei:{scope}/tei:author/tei:persName", namespaces=TEI_NS)
        if persons:
            for p in persons:
                forenames = p.xpath("tei:forename", namespaces=TEI_NS)
                surnames = p.xpath("tei:surname", namespaces=TEI_NS)
                fname = " ".join(_text(f) or "" for f in forenames).strip()
                sname = " ".join(_text(s) or "" for s in surnames).strip()
                full = f"{fname} {sname}".strip()
                if full:
                    names.append(full)
            break
    return names


def _extract_year(bibl_struct) -> int | None:
    date_el = bibl_struct.xpath("tei:monogr/tei:imprint/tei:date", namespaces=TEI_NS)
    candidates = []
    for d in date_el:
        when = d.get("when")
        if when:
            candidates.append(when)
        txt = _text(d)
        if txt:
            candidates.append(txt)
    for c in candidates:
        m = _YEAR_RE.search(c)
        if m:
            return int(m.group(1))
    return None


def _extract_title(bibl_struct) -> str | None:
    analytic_title = bibl_struct.xpath("tei:analytic/tei:title", namespaces=TEI_NS)
    if analytic_title:
        t = _text(analytic_title[0])
        if t:
            return t
    monogr_title = bibl_struct.xpath("tei:monogr/tei:title", namespaces=TEI_NS)
    if monogr_title:
        return _text(monogr_title[0])
    return None


def _extract_venue(bibl_struct) -> str | None:
    # If there was an analytic title, the monogr title is the venue
    # (journal/proceedings). If there was no analytic title, monogr/title
    # was already used as the reference's own title, so no separate venue.
    has_analytic = bool(bibl_struct.xpath("tei:analytic/tei:title", namespaces=TEI_NS))
    if not has_analytic:
        return None
    monogr_titles = bibl_struct.xpath("tei:monogr/tei:title", namespaces=TEI_NS)
    if monogr_titles:
        return _text(monogr_titles[0])
    meeting = bibl_struct.xpath("tei:monogr/tei:meeting", namespaces=TEI_NS)
    if meeting:
        return _text(meeting[0])
    return None


def _extract_doi(bibl_struct) -> str | None:
    idnos = bibl_struct.xpath(".//tei:idno[@type='DOI']", namespaces=TEI_NS)
    if idnos:
        t = _text(idnos[0])
        return t.strip() if t else None
    return None


def _extract_raw_text(bibl_struct) -> str | None:
    notes = bibl_struct.xpath("tei:note[@type='raw_reference']", namespaces=TEI_NS)
    if notes:
        return _text(notes[0])
    return None


def _extract_arxiv(bibl_struct, raw_text: str | None) -> str | None:
    idnos = bibl_struct.xpath(".//tei:idno", namespaces=TEI_NS)
    for idno in idnos:
        t = _text(idno) or ""
        m = _ARXIV_RE.search(t)
        if m:
            return m.group(1)
    if raw_text:
        m = _ARXIV_RE.search(raw_text)
        if m:
            return m.group(1)
    return None


def parse_tei_references(tei_xml: str, paper_id: str) -> list[RawReference]:
    """Parse GROBID's processReferences TEI output into RawReference list.
    Returns [] (rather than raising) if the document has no parseable
    bibliography -- callers should treat an empty result as worth flagging
    for manual inspection of that PDF, not silently skip it."""
    if not tei_xml or not tei_xml.strip():
        return []
    try:
        root = etree.fromstring(tei_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []

    bibl_structs = root.xpath("//tei:listBibl/tei:biblStruct", namespaces=TEI_NS)
    refs: list[RawReference] = []
    for i, bs in enumerate(bibl_structs):
        raw_text = _extract_raw_text(bs)
        title = _extract_title(bs)
        authors = _extract_authors(bs)
        year = _extract_year(bs)
        venue = _extract_venue(bs)
        doi = _extract_doi(bs)
        arxiv_id = _extract_arxiv(bs, raw_text)

        # If GROBID failed to segment a title but we do have the raw
        # citation text, keep the raw text so this reference isn't silently
        # dropped from downstream verification -- surfacing it as
        # low-confidence is preferable to losing it.
        if not title and raw_text:
            title = raw_text

        refs.append(
            RawReference(
                paper_id=paper_id,
                ref_index=i,
                raw_text=raw_text,
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                doi=doi,
                arxiv_id=arxiv_id,
            )
        )
    return refs
