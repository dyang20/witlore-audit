"""Parcels, from PublicDataEnterprise/PublicData_Parcels (layer 0).

Field names confirmed by the stage 0 probe. The layer carries more than
ownership: PRIMARYRURALZONING and PRIMARYCBZONING are the county's own
parcel-to-district join for the unincorporated county and Council Bluffs, and
CityCode gives jurisdiction directly (empty means unincorporated).

Those cached attributes are read but never trusted alone. zoning.py also does an
independent spatial join against the district polygons; agreement raises
confidence and both are cited, disagreement forces Unresolved. A cached
attribute can be stale and the assignment wants spatial evidence.
"""

from __future__ import annotations

from typing import Any, Optional

from ..evidence import Ledger
from ..geo.crs import area_sqft, geojson_to_working
from ..models import Parcel
from .base import FetchResult, clean, first_attr, query_layer

SERVICE = ("https://gis.pottcounty-ia.gov/arcgis/rest/services/"
           "PublicDataEnterprise/PublicData_Parcels/FeatureServer")
LAYER = 0

# Minimum share of a parcel that must fall inside the ring before it is listed.
# The human reports letter every parcel touching the ring, so this is low; it
# exists only to drop slivers from envelope-vs-ring edge effects.
MIN_RING_OVERLAP_SQFT = 100.0


def fetch(ring, ledger: Ledger) -> tuple[list[Parcel], FetchResult]:
    """Return parcels intersecting the ring, in the working CRS."""
    res = query_layer(
        SERVICE, LAYER,
        envelope_wgs84=ring.envelope_wgs84(),
        ring_area_sqft=ring.area_sqft(),
    )
    if not res.ok:
        return [], res

    parcels: list[Parcel] = []
    for feat in res.features:
        geom_json = feat.get("geometry")
        if not geom_json:
            continue
        try:
            geom = geojson_to_working(geom_json)
        except Exception:  # noqa: BLE001 - a bad geometry is one parcel, not a run
            res.warnings.append(f"unparseable geometry for "
                                f"{first_attr(feat, 'PIN', default='?')}")
            continue
        if geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)

        clipped = geom.intersection(ring.polygon)
        if clipped.is_empty or area_sqft(clipped) < MIN_RING_OVERLAP_SQFT:
            continue

        pin = clean(first_attr(feat, "PIN", "TAXPIN")) or "UNKNOWN"
        site_addr = _site_address(feat)

        p = Parcel(
            pin=pin,
            owner=clean(first_attr(feat, "OWNER")),
            site_address=site_addr,
            owner_address=clean(first_attr(feat, "TaxAddress")),
            net_acres=_as_float(first_attr(feat, "NETACRES", "PARCELAREA")),
            prop_class=clean(first_attr(feat, "PROPCLASS")),
            dwelling_value=_as_float(first_attr(feat, "DWELLVALUE")),
            city_code=clean(first_attr(feat, "CityCode")),
            legal=clean(first_attr(feat, "LEGAL")),
            deed_url=clean(first_attr(feat, "DEED")),
            geometry=geom,
            area_in_ring_sqft=area_sqft(clipped),
        )

        eid = ledger.from_dataset(
            {"pin": p.pin, "owner": p.owner, "net_acres": p.net_acres},
            f"Parcel {p.pin} ownership and acreage",
            url=f"{SERVICE}/{LAYER}", layer="PublicData_Parcels",
        )
        p.evidence_ids.append(eid)

        # The county's cached district attributes, recorded but cross-checked.
        rural = clean(first_attr(feat, "PRIMARYRURALZONING"))
        cb = clean(first_attr(feat, "PRIMARYCBZONING"))
        if rural or cb:
            p.evidence_ids.append(ledger.from_dataset(
                {"rural": rural, "council_bluffs": cb},
                f"County parcel attribute: primary zoning for {p.pin}",
                url=f"{SERVICE}/{LAYER}", layer="PublicData_Parcels",
            ))
        p._attr_zoning = {"rural": rural, "council_bluffs": cb}  # type: ignore[attr-defined]

        if p.dwelling_value is not None:
            p.evidence_ids.append(ledger.from_dataset(
                p.dwelling_value,
                f"Assessed dwelling value on {p.pin} "
                f"({'dwelling present' if p.dwelling_value > 0 else 'no assessed dwelling'})",
                url=f"{SERVICE}/{LAYER}", layer="PublicData_Parcels",
            ))

        parcels.append(p)

    merged = _merge_same_owner(parcels)
    res.count = len(merged)
    return merged, res


def _site_address(feat: dict) -> Optional[str]:
    """ADDRESS is often blank; HOUSENUM plus ADDRESS is the assembled form."""
    addr = clean(first_attr(feat, "ADDRESS"))
    num = clean(first_attr(feat, "HOUSENUM"))
    if addr and num and not addr.startswith(num):
        return f"{num} {addr}"
    return addr or None


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _merge_same_owner(parcels: list[Parcel]) -> list[Parcel]:
    """The DA training says parcels with the same owner share one letter.
    Contiguity is not required: the human reports letter by ownership."""
    by_owner: dict[str, list[Parcel]] = {}
    out: list[Parcel] = []
    for p in parcels:
        key = (p.owner or "").strip().upper()
        if not key:
            out.append(p)
            continue
        by_owner.setdefault(key, []).append(p)

    for owner, group in by_owner.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        primary = max(group, key=lambda q: q.area_in_ring_sqft or 0.0)
        primary.pin = " / ".join(sorted({q.pin for q in group}))
        primary.area_in_ring_sqft = sum(q.area_in_ring_sqft or 0.0 for q in group)
        # The merged parcel's geometry must be the union of the group, not just
        # the largest tract. Keeping only the largest meant a candidate holding
        # eight adjoining tracts was evaluated on one of them, which understates
        # the buildable area and can flip a Pursue to a Do not pursue. A union of
        # disjoint tracts yields a MultiPolygon, and the compound and anchor
        # tests handle that correctly: a square cannot span a gap between
        # pieces, which is the behaviour we want.
        from shapely.ops import unary_union
        geoms = [q.geometry for q in group if q.geometry is not None]
        if geoms:
            merged_geom = unary_union(geoms)
            primary.geometry = (merged_geom if merged_geom.is_valid
                                else merged_geom.buffer(0))
        primary.net_acres = sum(q.net_acres or 0.0 for q in group) or primary.net_acres
        # Keep the largest parcel's geometry as the representative shape; the
        # other tracts stay available for the map.
        primary._merged_geoms = [q.geometry for q in group]  # type: ignore[attr-defined]
        if any((q.dwelling_value or 0) > 0 for q in group):
            primary.dwelling_value = max((q.dwelling_value or 0) for q in group)
        out.append(primary)
    return out
