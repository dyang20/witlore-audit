"""The evidence ledger.

Every factual claim in a generated report points at a record here. The report
builder refuses to render a statement whose evidence ids are not in the ledger,
so an unsourced claim fails loudly at build time instead of reading as fact.

Ids are stable within a run: E1, E2, ... in creation order.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import date
from typing import Any, Iterable, Optional

from .models import Evidence, SourceType


class MissingEvidence(KeyError):
    """Raised when a conclusion references an evidence id that was never recorded."""


class Ledger:
    """Append-only record of everything a report relies on.

    Thread-safe for recording. Several sources are fetched concurrently and
    all of them write here, and `self._n += 1` is a read-modify-write that two
    threads can interleave: without the lock two records can be handed the
    same id, and the second silently overwrites the first. A citation that
    resolves to the wrong record is worse than one that fails to resolve, so
    this is guarded rather than left to the GIL.
    """

    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}
        self._n = 0
        self._lock = threading.Lock()

    # -- recording ---------------------------------------------------------

    def _next_id(self) -> str:
        self._n += 1
        return f"E{self._n}"

    def add(
        self,
        value: Any,
        source_type: SourceType,
        description: str,
        *,
        url: Optional[str] = None,
        section: Optional[str] = None,
        quote: Optional[str] = None,
        retrieved_at: Optional[str] = None,
        snapshot_hash: Optional[str] = None,
        derived_from: Optional[list[str]] = None,
    ) -> str:
        if source_type is SourceType.DOCUMENT and not quote:
            raise ValueError("document evidence requires a verbatim quote")
        if source_type is SourceType.DOCUMENT and not url and not snapshot_hash:
            raise ValueError("document evidence requires a url or a snapshot hash")
        if source_type is SourceType.COMPUTED and not derived_from:
            raise ValueError("computed evidence must name the evidence it derives from")
        with self._lock:
            for parent in derived_from or []:
                if parent not in self._items:
                    raise MissingEvidence(
                        f"derived_from references unknown evidence {parent}")

            eid = self._next_id()
            self._items[eid] = Evidence(
                id=eid, value=value, source_type=source_type,
                description=description,
                url=url, section=section, quote=quote,
                retrieved_at=retrieved_at or date.today().isoformat(),
                snapshot_hash=snapshot_hash, derived_from=list(derived_from or []),
            )
        return eid

    # Convenience wrappers; they exist so call sites read as what they are.

    def from_input(self, value: Any, description: str, column: str) -> str:
        return self.add(value, SourceType.INPUT, description, section=column)

    def from_dataset(self, value: Any, description: str, url: str,
                     layer: Optional[str] = None) -> str:
        return self.add(value, SourceType.DATASET, description, url=url, section=layer)

    def from_document(self, value: Any, description: str, *, url: Optional[str],
                      section: str, quote: str, retrieved_at: str,
                      snapshot_hash: Optional[str] = None) -> str:
        return self.add(value, SourceType.DOCUMENT, description, url=url,
                        section=section, quote=quote, retrieved_at=retrieved_at,
                        snapshot_hash=snapshot_hash)

    def computed(self, value: Any, description: str, derived_from: list[str]) -> str:
        return self.add(value, SourceType.COMPUTED, description, derived_from=derived_from)

    def from_human(self, value: Any, description: str, who: str) -> str:
        return self.add(value, SourceType.HUMAN, description, section=who)

    # -- reading -----------------------------------------------------------

    def __contains__(self, eid: object) -> bool:
        return eid in self._items

    def __getitem__(self, eid: str) -> Evidence:
        if eid not in self._items:
            raise MissingEvidence(eid)
        return self._items[eid]

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> dict[str, Evidence]:
        return dict(self._items)

    def require(self, ids: Iterable[str]) -> None:
        """Called by the report builder before rendering a statement."""
        missing = [i for i in ids if i not in self._items]
        if missing:
            raise MissingEvidence(f"unsourced claim references {missing}")

    # -- output ------------------------------------------------------------

    def provenance_rows(self) -> list[dict[str, Any]]:
        """One row per evidence id, for the report appendix."""
        rows = []
        for eid, e in self._items.items():
            rows.append({
                "id": eid,
                "type": e.source_type.value,
                "claim": e.description,
                "value": _short(e.value),
                "source": e.url or e.section or "",
                "section": e.section or "",
                "quote": (e.quote or "")[:400],
                "retrieved": e.retrieved_at or "",
                "derived_from": ", ".join(e.derived_from),
            })
        return rows

    def to_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.provenance_rows(), fh, indent=2, default=str)


def _short(v: Any, limit: int = 200) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
