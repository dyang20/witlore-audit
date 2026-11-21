"""Zoning districts for both jurisdictions, with a cross-check.

Two independent determinations per parcel:
  - spatial join against the district polygons
  - the county's cached PRIMARYRURALZONING / PRIMARYCBZONING parcel attribute

Agreement is recorded as 'both' and raises confidence. Disagreement produces
District(source='conflict') and an Unresolved, because a cached attribute can be
stale and a silent pick between them would be exactly the kind of unsourced
conclusion this pipeline exists to avoid.
"""

from __future__ import annotations

from typing import Any, Optional

from ..evidence import Ledger
from ..geo.crs import area_sqft, geojson_to_working
from ..models import District, Parcel, Unresolved
from .base import FetchResult, clean, first_attr, query_layer

COUNTY_SERVICE = ("https://gis.pottcounty-ia.gov/arcgis/rest/services/"
                  "Hosted/Zoning_Pottawattamie/FeatureServer")
CB_SERVICE = ("https://gispublic.councilbluffs-ia.gov/publicserver/rest/services/"
              "CommDev_Public/cbZoningDistricts_Public/FeatureServer")
LAYER = 0

# A district polygon must cover at least this much of a parcel's in-ring area
# before it is listed, so a boundary sliver does not create a phantom district.
MIN_DISTRICT_SHARE = 0.02


def fetch_districts(ring, ledger: Ledger) -> tuple[list[dict], list[FetchResult]]:
    """District polygons from both services, in the working CRS."""
    out: list[dict] = []
    results: list[FetchResult] = []
    env = ring.envelope_wgs84()

    for jurisdiction, service in (("Pottawattamie County", COUNTY_SERVICE),
                                  ("Council Bluffs", CB_SERVICE)):
        res = query_layer(service, LAYER, envelope_wgs84=env,
                          ring_area_sqft=ring.area_sqft())
        results.append(res)
        if not res.ok:
            continue
        for feat in res.features:
            gj = feat.get("geometry")
            if not gj:
                continue
            try:
                geom = geojson_to_working(gj)
            except Exception:  # noqa: BLE001
                continue
            if geom.is_empty:
                continue
            if not geom.is_valid:
                geom = geom.buffer(0)
            code = _district_code(feat)
            if not code:
                continue
            out.append({
                "jurisdiction": jurisdiction,
                "code": code,
                "name": _district_name(feat),
                "geometry": geom,
                "service": service,
                "ordinance_link": clean(first_attr(feat, "MunicodeLink", "OrdinanceLink")),
                "ordinance_number": clean(first_attr(feat, "OrdinanceNumber", "ORD_NUM")),
            })
    return out, results


def assign(parcel: Parcel, districts: list[dict], ring, ledger: Ledger) -> None:
    """Attach districts to a parcel and reconcile against the cached attribute."""
    in_ring = parcel.geometry.intersection(ring.polygon)
    if in_ring.is_empty:
        return
    total = area_sqft(in_ring)

    spatial: list[tuple[str, str, float, dict]] = []
    for d in districts:
        overlap = in_ring.intersection(d["geometry"])
        if overlap.is_empty:
            continue
        share = area_sqft(overlap) / total if total else 0.0
        if share < MIN_DISTRICT_SHARE:
            continue
        spatial.append((d["jurisdiction"], d["code"], share, d))

    spatial.sort(key=lambda t: t[2], reverse=True)
    attr = getattr(parcel, "_attr_zoning", {}) or {}

    # A parcel legitimately spans several districts, and the county's cached
    # attribute records only the PRIMARY one. So a conflict means the cached
    # value matches none of the districts found spatially, not that it differs
    # from one of them. Treating every secondary district as a conflict made
    # ordinary split parcels unresolvable.
    codes_by_jurisdiction: dict[str, list[str]] = {}
    for jurisdiction, code, _share, _d in spatial:
        codes_by_jurisdiction.setdefault(jurisdiction, []).append(code)
    conflicts: set[str] = set()
    for jurisdiction, codes in codes_by_jurisdiction.items():
        cached = attr.get("rural" if jurisdiction == "Pottawattamie County"
                          else "council_bluffs")
        if cached and not any(_same_district(cached, c) for c in codes):
            conflicts.add(jurisdiction)

    for jurisdiction, code, share, d in spatial:
        cached = attr.get("rural" if jurisdiction == "Pottawattamie County"
                          else "council_bluffs")
        eids = [ledger.from_dataset(
            {"district": code, "share_of_parcel_in_ring": round(share, 3)},
            f"Spatial join: parcel {parcel.pin} falls in {jurisdiction} district {code} "
            f"({share:.0%} of its in-ring area)",
            url=f"{d['service']}/{LAYER}", layer="zoning districts",
        )]

        source = "spatial_join"
        if cached and _same_district(cached, code):
            source = "both"
            eids.append(ledger.computed(
                code,
                f"County parcel attribute agrees with the spatial join for "
                f"{parcel.pin}: {code}",
                parcel.evidence_ids[:1] + eids[:1],
            ))
        elif jurisdiction in conflicts:
            source = "conflict"
            if not any("contested" in u.what_is_missing for u in parcel.unresolved):
                parcel.unresolved.append(Unresolved(
                    what_is_missing=f"Zoning district for parcel {parcel.pin} is "
                                    f"contested: the county parcel attribute says "
                                    f"{cached}, the zoning layer shows "
                                    f"{', '.join(codes_by_jurisdiction[jurisdiction])}",
                    why_it_matters="District determines whether a tower is permitted, "
                                   "conditional or prohibited, and sets the height cap "
                                   "and setbacks. The two sources cannot both be right.",
                    how_to_resolve=f"Confirm with the {jurisdiction} planning department, "
                                   f"or check the Zoning Document Index for a rezoning "
                                   f"case on this parcel.",
                ))

        parcel.districts.append(District(
            code=code, name=d.get("name"), jurisdiction=jurisdiction,
            source=source, evidence_ids=eids,
        ))

    if not spatial:
        _no_spatial_district(parcel, attr, ledger)


def _no_spatial_district(parcel: Parcel, attr: dict, ledger: Ledger) -> None:
    """No polygon covered the parcel. Either genuinely unzoned, or a data gap."""
    # Which attribute supplied the value decides which ordinance applies. An
    # earlier version filed a PRIMARYCBZONING value under the county, so a city
    # parcel with no polygon was judged against the county's district of the
    # same name, which is a different district under a different code.
    if attr.get("rural"):
        cached, jurisdiction, field_name = (
            attr["rural"], "Pottawattamie County", "PRIMARYRURALZONING")
    elif attr.get("council_bluffs"):
        cached, jurisdiction, field_name = (
            attr["council_bluffs"], "Council Bluffs", "PRIMARYCBZONING")
    else:
        cached, jurisdiction, field_name = None, None, None
    if cached:
        parcel.districts.append(District(
            code=cached, jurisdiction=jurisdiction,
            source="parcel_attribute",
            evidence_ids=[ledger.from_dataset(
                cached,
                f"County parcel attribute {field_name} gives {jurisdiction} district "
                f"{cached} for {parcel.pin}; no zoning polygon covered this parcel",
                url="PublicData_Parcels", layer=field_name,
            )],
        ))
        parcel.unresolved.append(Unresolved(
            what_is_missing=f"No zoning polygon covers parcel {parcel.pin}; the district "
                            f"is taken from the parcel attribute only",
            why_it_matters="Without a mapped district the setback and height rules rest "
                           "on a single cached value with no spatial confirmation.",
            how_to_resolve="Confirm the district with the planning department, or obtain "
                           "a current zoning map for this area.",
            blocking=False,
        ))
    elif parcel.jurisdiction and parcel.jurisdiction != "Pottawattamie County":
        # Inside a municipality whose districts are not in any layer we can
        # reach. The county zoning service covers unincorporated land only, and
        # smaller cities often publish no zoning GIS at all: the TI-OPP-43986
        # human audit records obtaining the City of Oakland map by email from
        # the Assistant City Clerk. Naming the city and the reason is far more
        # useful than repeating a generic "no district" line per parcel.
        parcel.unresolved.append(Unresolved(
            what_is_missing=f"No zoning district layer is available for "
                            f"{parcel.jurisdiction}, so parcel {parcel.pin} has no "
                            f"mapped district",
            why_it_matters="District sets the use permission, height cap and "
                           "setbacks. The jurisdiction's written rules are loaded, "
                           "but without a district the parcel cannot be matched to "
                           "them.",
            how_to_resolve=f"Request the current zoning map from {parcel.jurisdiction} "
                           f"and georeference it, or ask the clerk for the district of "
                           f"the specific parcels of interest.",
        ))
    else:
        parcel.unresolved.append(Unresolved(
            what_is_missing=f"No zoning district found for parcel {parcel.pin}",
            why_it_matters="Could mean the area is unzoned (which would make this a "
                           "Level 1 site and a strong candidate) or that the zoning "
                           "layer is incomplete. The two have opposite implications.",
            how_to_resolve="Call the county planning department to confirm whether this "
                           "area is zoned. The DA training's guidance on no-zoning "
                           "findings is to confirm directly.",
        ))


def _district_code(feat: dict) -> Optional[str]:
    return clean(first_attr(feat, "ZONING", "Zoning", "ZONE", "ZONECODE",
                            "DISTRICT", "ZONE_CODE", "Zone", "CLASS", "ZoningCode"))


def _district_name(feat: dict) -> Optional[str]:
    return clean(first_attr(feat, "ZONINGDESCRIPTION", "DESCRIPTION", "Description",
                            "ZONE_DESC", "NAME", "Name"))


def _same_district(a: str, b: str) -> bool:
    """'A2', 'A-2' and 'Class A-2' are the same district."""
    norm = lambda s: "".join(ch for ch in str(s).upper() if ch.isalnum())
    na, nb = norm(a), norm(b)
    return na == nb or na.endswith(nb) or nb.endswith(na)
