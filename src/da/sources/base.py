"""Shared fetch machinery for external data sources.

Two failure modes this module exists to prevent, both found during stage 0:

1. A non-spatial table silently ignores a spatial filter. `Address Table` has
   geometryType None; querying it with an envelope returned all 35,087 county
   rows with no error. Every spatial query checks geometry type first, and
   results are sanity-checked against the ring area.

2. Server-reported areas are not trustworthy. The same building footprint
   reports 1,019.8 in one county service and 167.9 in another. Geometry comes
   back as GeoJSON and areas are computed locally in EPSG:102676.

Source modules return (data, evidence_ids). They never interpret, and never
raise on a dead endpoint: a failure becomes an Unresolved the report can print.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import requests

from ..models import Unresolved

USER_AGENT = "witlore-audit/0.1"
TIMEOUT = 45
RETRIES = 4
BACKOFF = 2.0

# A 3,281 ft ring is ~776 acres. More features than this in a ring-scoped query
# means the spatial filter was ignored.
IMPLAUSIBLE_FEATURE_COUNT = 5000


class SourceError(Exception):
    pass


@dataclass
class FetchResult:
    """What every source module returns alongside its parsed data."""

    ok: bool
    features: list[dict] = field(default_factory=list)
    url: str = ""
    layer: str = ""
    retrieved_at: str = field(default_factory=lambda: date.today().isoformat())
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    count: int = 0

    def as_unresolved(self, what: str, why: str, how: str) -> Unresolved:
        return Unresolved(
            what_is_missing=f"{what} ({self.error})",
            why_it_matters=why,
            how_to_resolve=how,
        )


class _Permanent(SourceError):
    """A failure that will not change on a second attempt."""


def http_json(url: str, params: Optional[dict] = None,
              retries: int = RETRIES, timeout: int = TIMEOUT) -> dict[str, Any]:
    """GET returning parsed JSON. ArcGIS returns HTTP 200 with an error body,
    so that case is converted into a real failure.

    Only transient failures are retried. A 4xx, or an ArcGIS error body with a
    4xx code ("Layer not found", "Invalid query"), is the server answering
    correctly that the request is wrong; repeating it with backoff spent seven
    seconds per call to learn nothing, which is how one misnumbered layer added
    most of a minute to a run.
    """
    last: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": USER_AGENT})
            if 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                raise _Permanent(f"HTTP {r.status_code} from {url}")
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "error" in data:
                err = data["error"] or {}
                msg = f"ArcGIS error {err.get('code')}: {err.get('message')}"
                code = err.get("code")
                if isinstance(code, int) and 400 <= code < 500 and code not in (408, 429, 498, 499):
                    raise _Permanent(msg)
                raise SourceError(msg)
            return data
        except _Permanent as e:
            raise SourceError(str(e)) from e
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(BACKOFF ** attempt)
    if isinstance(last, SourceError):
        raise SourceError(str(last))
    raise SourceError(f"{type(last).__name__}: {last}")


def layer_metadata(service_url: str, layer_id: int, *, retries: int = RETRIES,
                   timeout: int = TIMEOUT) -> dict[str, Any]:
    return http_json(f"{service_url.rstrip('/')}/{layer_id}", {"f": "json"},
                     retries=retries, timeout=timeout)


def is_spatial(meta: dict) -> bool:
    """Guard against the Address Table trap."""
    return bool(meta.get("geometryType"))


def query_layer(
    service_url: str,
    layer_id: int,
    *,
    envelope_wgs84: Optional[dict] = None,
    where: str = "1=1",
    out_fields: str = "*",
    return_geometry: bool = True,
    ring_area_sqft: Optional[float] = None,
    page_size: int = 1000,
    retries: int = RETRIES,
    timeout: int = TIMEOUT,
) -> FetchResult:
    """Spatially query a layer and return GeoJSON features in WGS84.

    Pages through results. Refuses to run a spatial query against a table.
    """
    url = f"{service_url.rstrip('/')}/{layer_id}/query"
    res = FetchResult(ok=False, url=f"{service_url.rstrip('/')}/{layer_id}")

    try:
        meta = layer_metadata(service_url, layer_id, retries=retries, timeout=timeout)
    except SourceError as e:
        res.error = f"layer metadata unavailable: {e}"
        return res
    res.layer = meta.get("name", str(layer_id))

    if envelope_wgs84 and not is_spatial(meta):
        res.error = (f"layer '{res.layer}' is a table (geometryType null); a spatial "
                     f"filter would be ignored. Join by PIN instead.")
        return res

    params: dict[str, Any] = {
        "f": "geojson", "where": where, "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "outSR": 4326, "resultRecordCount": page_size,
    }
    if envelope_wgs84:
        params.update({
            "geometry": json.dumps(envelope_wgs84),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
        })

    features: list[dict] = []
    offset = 0
    while True:
        try:
            page = http_json(url, {**params, "resultOffset": offset},
                             retries=retries, timeout=timeout)
        except SourceError as e:
            if features:
                res.warnings.append(f"paging stopped at {len(features)} features: {e}")
                break
            res.error = str(e)
            return res
        got = page.get("features") or []
        features.extend(got)
        if len(got) < page_size or len(features) >= IMPLAUSIBLE_FEATURE_COUNT:
            break
        offset += page_size

    res.features = features
    res.count = len(features)
    res.ok = True

    if envelope_wgs84 and len(features) >= IMPLAUSIBLE_FEATURE_COUNT:
        res.warnings.append(
            f"{len(features)} features returned for a ring-scoped query; the spatial "
            f"filter may have been ignored. Treat this layer as unverified.")
    if ring_area_sqft and len(features) > 2000:
        res.warnings.append(
            f"{len(features)} features in a {ring_area_sqft / 43560:.0f}-acre ring "
            f"is unusually dense; check the layer.")
    return res


def first_attr(feature: dict, *names: str, default: Any = None) -> Any:
    """Field names vary in case between county services (TAXPIN vs taxpin)."""
    props = feature.get("properties") or {}
    lowered = {k.lower(): v for k, v in props.items()}
    for n in names:
        if n in props:
            return props[n]
        if n.lower() in lowered:
            return lowered[n.lower()]
    return default


def clean(value: Any) -> Optional[str]:
    """County services use '' and ' ' for null."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None
