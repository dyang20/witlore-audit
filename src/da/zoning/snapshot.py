"""Ordinance snapshots and quote verification.

A snapshot is the ordinance text plus its URL, retrieval date and SHA-256. Every
quoted passage in a report must be a literal substring of a stored snapshot;
verify() is what makes a fabricated section number fail automatically rather
than reading as authoritative.

The Pottawattamie text was supplied by the hiring team with no source URL and
no retrieval date (its newest internal amendment is #2015-05, 2015-12-18). It is
stored with source_type 'supplied' and every citation drawn from it says so,
because an unverifiable source should not look like a verified one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

SNAPSHOT_DIR = "data/ordinances"


@dataclass
class Snapshot:
    jurisdiction: str
    text: str
    url: Optional[str]
    retrieved_at: str
    sha256: str
    # retrieved   = fetched from the official source
    # supplied    = handed over by the client, no verifiable origin
    # third_party_mirror = a mirror of the official code; substance likely
    #               right, but quotations cannot be checked against authority
    source_type: str = "retrieved"
    note: Optional[str] = None

    @property
    def is_verifiable(self) -> bool:
        return self.source_type == "retrieved" and bool(self.url)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def save(snapshot: Snapshot, directory: str = SNAPSHOT_DIR) -> str:
    d = os.path.join(directory, _slug(snapshot.jurisdiction))
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, snapshot.retrieved_at)
    with open(base + ".txt", "w", encoding="utf-8") as fh:
        fh.write(snapshot.text)
    meta = {k: v for k, v in snapshot.__dict__.items() if k != "text"}
    with open(base + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return base + ".txt"


def load(jurisdiction: str, directory: str = SNAPSHOT_DIR) -> Optional[Snapshot]:
    """Most recent snapshot for a jurisdiction."""
    d = os.path.join(directory, _slug(jurisdiction))
    if not os.path.isdir(d):
        return None
    metas = sorted(f for f in os.listdir(d) if f.endswith(".meta.json"))
    if not metas:
        return None
    with open(os.path.join(d, metas[-1]), encoding="utf-8") as fh:
        meta = json.load(fh)
    txt = os.path.join(d, metas[-1].replace(".meta.json", ".txt"))
    with open(txt, encoding="utf-8") as fh:
        text = fh.read()
    return Snapshot(text=text, **meta)


def ingest_file(path: str, jurisdiction: str, *, url: Optional[str] = None,
                source_type: str = "supplied", note: Optional[str] = None,
                directory: str = SNAPSHOT_DIR) -> Snapshot:
    """Store a local ordinance file as a snapshot.

    The supplied Pottawattamie file carries a .pdf extension but is plain UTF-8
    text with CRLF line endings, so it is read as text and line endings are
    normalised before hashing.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    snap = Snapshot(
        jurisdiction=jurisdiction, text=text, url=url,
        retrieved_at=date.today().isoformat(), sha256=sha256_text(text),
        source_type=source_type, note=note,
    )
    save(snap, directory)
    return snap


# -- verification ----------------------------------------------------------

# Ordinance PDFs are full of typographic punctuation. Normalising these is not
# loosening the check: a curly apostrophe and a straight one are the same
# character to a reader, and refusing the match would reject true quotes.
_PUNCT = {
    "‘": "'", "’": "'", "‛": "'",
    "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "\u0002": "", "﻿": "",   # soft hyphen artefacts from the source file
}


def _normalise(s: str) -> str:
    """Collapse whitespace and fold typographic punctuation so a quote spanning
    a line break, or using curly quotes, still matches."""
    for bad, good in _PUNCT.items():
        s = s.replace(bad, good)
    return re.sub(r"\s+", " ", s).strip()


def verify_quote(quote: str, snapshot: Snapshot,
                 section: Optional[str] = None,
                 window: int = 4000) -> bool:
    """A quote counts only if it appears verbatim in the snapshot.

    When `section` is given, the quote must also appear *near* that section
    heading, not merely somewhere in the document. Without that check a quote
    attributed to an invented section passes whenever identical wording exists
    elsewhere, which is exactly what happened: sections 8.050.020, 8.055.020 and
    8.060.020 were cited for districts whose chapters contain no tower clause,
    and the substring check accepted them because the same sentence appears in
    C-1 and C-3. A citation that points at the wrong place is a worse failure
    than a missing one, because it looks authoritative.
    """
    if not quote or not quote.strip():
        return False
    text = _normalise(snapshot.text)
    nq = _normalise(quote)
    if nq not in text:
        return False
    if not section:
        return True

    # Bound the search by the section's real extent rather than a fixed window:
    # §8.004.220 runs to eighteen subsections, so a window large enough for it
    # would be large enough to leak into neighbouring districts.
    for body in section_extents(snapshot.text, section, max_chars=window):
        if nq in _normalise(body):
            return True
    return False


_HEADING = re.compile(r"^\d+\.\d{3}\.\d{3}\b", re.MULTILINE)


def section_extents(raw: str, section: str, max_chars: int = 4000) -> list[str]:
    """Every block of text belonging to `section`, by line structure.

    Subsections are written as bare `.12` lines beneath their parent heading
    rather than as `8.004.220.12`, so a subsection citation resolves to its
    parent's extent. The extent ends at the next section heading, which is what
    makes the check tight without being arbitrary.
    """
    candidates = [section]
    parts = section.split(".")
    if len(parts) > 3:
        candidates.append(".".join(parts[:3]))

    out: list[str] = []
    for cand in candidates:
        pattern = re.compile(rf"^{re.escape(cand)}\b", re.MULTILINE)
        for m in pattern.finditer(raw):
            nxt = _HEADING.search(raw, m.end())
            end = min(nxt.start() if nxt else len(raw), m.start() + max_chars * 4)
            out.append(raw[m.start():end])
        if out:
            break
    return out


def find_section(snapshot: Snapshot, section: str, max_chars: int = 2500) -> Optional[str]:
    """Return the text of a numbered section, for extraction and citation."""
    pattern = re.compile(rf"^{re.escape(section)}\b", re.MULTILINE)
    m = pattern.search(snapshot.text)
    if not m:
        return None
    start = m.start()
    nxt = re.compile(r"^\d+\.\d{3}\.\d{3}\b", re.MULTILINE).search(snapshot.text,
                                                                   m.end())
    end = min(nxt.start(), start + max_chars) if nxt else start + max_chars
    return snapshot.text[start:end].strip()


class QuoteRejected(ValueError):
    """Raised when a rule's quote is not found in the snapshot."""


def require_quote(quote: str, snapshot: Snapshot, section: str) -> str:
    if not verify_quote(quote, snapshot):
        raise QuoteRejected(
            f"quote for {section} is not a literal substring of the "
            f"{snapshot.jurisdiction} snapshot ({snapshot.sha256[:12]})")
    return quote
