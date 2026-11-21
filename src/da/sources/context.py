"""Context layers: jurisdiction, dwellings, wetlands, flood, overlays, utilities.

Each fetcher returns (geometries_or_records, FetchResult). None of them decide
anything; disposition.py applies them.

Dwelling detection combines three independent signals, which is the part the
human reports did by eye:
  - DWELLVALUE > 0 on the parcel (an assessed dwelling exists)
  - a building footprint classed 'R', joined by TAXPIN
  - an address point inside the parcel
A footprint with no assessed dwelling value is probably a barn or shed, which is
the false-positive problem that makes footprints alone a poor proxy.
"""

from __future__ import annotations

from typing import Any, Optional

from shapely.geometry import shape as shapely_shape
from shapely.ops import unary_union

from ..evidence import Ledger
from ..geo.crs import geojson_to_working
from .base import FetchResult, SourceError, clean, first_attr, http_json, query_layer

COUNTY = "https://gis.pottcounty-ia.gov/arcgis/rest/services"

MUNICIPAL = f"{COUNTY}/PublicDataEnterprise/PublicData_Municipal_Boundaries/FeatureServer"
BUILDINGS = f"{COUNTY}/PublicDataEnterprise/PublicData_Building_Footprints_2024/FeatureServer"
ADDRESS_POINTS = f"{COUNTY}/PublicDataEnterprise/PublicData_Address_Points/FeatureServer"
WETLANDS_COUNTY = (f"{COUNTY}/Hosted/"
                   "USFS_Wetlands_Dataset_Pottawattamie_County_6_6_2023/FeatureServer")
LOESS_HILLS = f"{COUNTY}/Hosted/Special_Landscape_Areas_Loess_Hills/FeatureServer"
SLOPE_OVERLAY = f"{COUNTY}/Hosted/Slope_Protection_Overlay_District_view/FeatureServer"
AIRPORT_HAZARD = f"{COUNTY}/Hosted/Airport_Hazard_Polygons_view/FeatureServer"
TWO_MILE = f"{COUNTY}/Hosted/Two_Mile_Area/FeatureServer"
ELECTRIC = f"{COUNTY}/Hosted/Electric_Service_Areas/FeatureServer"
ZONING_DOC_INDEX = f"{COUNTY}/Hosted/Zoning_Document_Index_View/FeatureServer"

NWI = "https://www.fws.gov/wetlandsmapservice/rest/services/Wetlands/MapServer"
NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
# NFHL layer 28 is Flood Hazard Zones in the published service; confirmed
# reachable by the stage 0 probe, which also found FIRM Panels and Flood Hazard
# Boundaries. The id is config so a service reorganisation is a one-line fix.
NFHL_FLOOD_ZONE_LAYER = 28


def _polys(res: FetchResult) -> list[Any]:
    out = []
    for f in res.features:
        gj = f.get("geometry")
        if not gj:
            continue
        try:
            g = geojson_to_working(gj)
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty:
            continue
        out.append(g if g.is_valid else g.buffer(0))
    return out


_FIRST_LAYER: dict[str, int] = {}


def _first_layer_id(service: str) -> int:
    """Hosted feature services do not always start at layer 0.

    Two_Mile_Area returns 404 for layer 0: its single layer carries a different
    id. Discovering it from the service root costs one cached request and
    removes a whole class of silent-miss bug, since a 404 here would otherwise
    read as "no features in this ring".
    """
    if service in _FIRST_LAYER:
        return _FIRST_LAYER[service]
    layer_id = 0
    try:
        meta = http_json(service, {"f": "json"}, retries=2, timeout=ADVISORY_TIMEOUT)
        layers = meta.get("layers") or []
        if layers:
            layer_id = int(layers[0].get("id", 0))
    except SourceError:
        pass
    _FIRST_LAYER[service] = layer_id
    return layer_id


# Advisory layers (overlays, utilities, case history) inform the report but
# never decide a parcel, so they get one retry and a short timeout: a slow
# overlay server must not be able to hold up a run.
ADVISORY_RETRIES = 2
ADVISORY_TIMEOUT = 20


def _simple(service: str, ring, layer: Optional[int] = None,
            advisory: bool = False) -> FetchResult:
    lid = _first_layer_id(service) if layer is None else layer
    extra = ({"retries": ADVISORY_RETRIES, "timeout": ADVISORY_TIMEOUT}
             if advisory else {})
    return query_layer(service, lid, envelope_wgs84=ring.envelope_wgs84(),
                       ring_area_sqft=ring.area_sqft(), **extra)


# -- jurisdiction ----------------------------------------------------------

def municipal_boundaries(ring, ledger: Ledger) -> tuple[list[dict], FetchResult]:
    res = _simple(MUNICIPAL, ring)
    out = []
    if res.ok:
        for f in res.features:
            gj = f.get("geometry")
            if not gj:
                continue
            try:
                out.append({"city": clean(first_attr(f, "City")),
                            "township": clean(first_attr(f, "Township_Code")),
                            "geometry": geojson_to_working(gj)})
            except Exception:  # noqa: BLE001
                continue
    return out, res


def jurisdiction_for(parcel, boundaries: list[dict], ledger: Ledger) -> str:
    """CityCode on the parcel is the primary signal; the boundary layer confirms."""
    cached = (parcel.city_code or "").strip()
    hit = None
    for b in boundaries:
        if b["geometry"].intersects(parcel.geometry):
            if b["geometry"].intersection(parcel.geometry).area > parcel.geometry.area * 0.5:
                hit = b
                break
    if hit and hit.get("city"):
        name = str(hit["city"]).title()
        parcel.evidence_ids.append(ledger.from_dataset(
            name, f"Parcel {parcel.pin} lies within the municipal boundary of {name}",
            url=f"{MUNICIPAL}/0", layer="Municipal Boundaries"))
        return name
    if cached:
        parcel.evidence_ids.append(ledger.from_dataset(
            cached, f"Parcel {parcel.pin} carries CityCode {cached}",
            url="PublicData_Parcels", layer="CityCode"))
        return cached
    parcel.evidence_ids.append(ledger.from_dataset(
        "unincorporated",
        f"Parcel {parcel.pin} has no CityCode and falls outside all municipal "
        f"boundaries: unincorporated Pottawattamie County",
        url=f"{MUNICIPAL}/0", layer="Municipal Boundaries"))
    return "Pottawattamie County"


# -- dwellings -------------------------------------------------------------

def dwellings(ring, ledger: Ledger) -> tuple[dict[str, list[Any]], list[FetchResult]]:
    """Returns {'footprints': [...], 'residential': [...], 'addresses': [...]}
    with geometries in the working CRS, plus per-source results."""
    results = []
    data: dict[str, list[Any]] = {"footprints": [], "residential": [], "addresses": []}

    b = _simple(BUILDINGS, ring)
    results.append(b)
    if b.ok:
        for f in b.features:
            gj = f.get("geometry")
            if not gj:
                continue
            try:
                g = geojson_to_working(gj)
            except Exception:  # noqa: BLE001
                continue
            if g.is_empty:
                continue
            rec = {"geometry": g,
                   "taxpin": clean(first_attr(f, "TAXPIN", "taxpin")),
                   "cls": clean(first_attr(f, "Class")),
                   "exempt": clean(first_attr(f, "Exempt"))}
            data["footprints"].append(rec)
            if (rec["cls"] or "").upper().startswith("R"):
                data["residential"].append(rec)

    a = _simple(ADDRESS_POINTS, ring)
    results.append(a)
    if a.ok:
        for f in a.features:
            gj = f.get("geometry")
            if not gj:
                continue
            try:
                data["addresses"].append(
                    {"geometry": geojson_to_working(gj),
                     "address": clean(first_attr(f, "Full_Address"))})
            except Exception:  # noqa: BLE001
                continue
    return data, results


def dwelling_points_for(parcel, dwelling_data: dict) -> list[Any]:
    """Dwelling locations relevant to this parcel's neighbours.

    Sec. 8.004.220.13 measures to *adjacent* dwellings, so every nearby dwelling
    counts, not only those on the subject parcel.
    """
    pts = []
    for rec in dwelling_data.get("residential", []):
        pts.append(rec["geometry"].centroid)
    for rec in dwelling_data.get("addresses", []):
        pts.append(rec["geometry"])
    return pts


# -- environmental ---------------------------------------------------------

def wetlands(ring, ledger: Ledger) -> tuple[list[Any], FetchResult]:
    res = query_layer(NWI, 0, envelope_wgs84=ring.envelope_wgs84(),
                      ring_area_sqft=ring.area_sqft())
    if not res.ok:
        res2 = _simple(WETLANDS_COUNTY, ring)
        res2.warnings.append(f"federal NWI unavailable ({res.error}); used the "
                             f"county USFS wetlands copy dated 2023-06-06")
        if res2.ok and res2.features:
            ledger.from_dataset(
                len(res2.features),
                f"{len(res2.features)} wetland polygons in the ring "
                f"(county USFS dataset, 2023-06-06)",
                url=f"{WETLANDS_COUNTY}/0", layer="wetlands")
        return _polys(res2), res2
    if res.features:
        ledger.from_dataset(
            len(res.features),
            f"{len(res.features)} NWI wetland polygons intersect the ring",
            url=f"{NWI}/0", layer="Wetlands")
    return _polys(res), res


def flood_zones(ring, ledger: Ledger) -> tuple[list[dict], FetchResult]:
    """Flood is a risk flag, never a disqualifier: Tillman's instruction."""
    res = query_layer(NFHL, NFHL_FLOOD_ZONE_LAYER,
                      envelope_wgs84=ring.envelope_wgs84(),
                      ring_area_sqft=ring.area_sqft())
    out = []
    if res.ok:
        for f in res.features:
            gj = f.get("geometry")
            if not gj:
                continue
            try:
                g = geojson_to_working(gj)
            except Exception:  # noqa: BLE001
                continue
            zone = clean(first_attr(f, "FLD_ZONE", "ZONE"))
            out.append({"geometry": g, "zone": zone,
                        "panel": clean(first_attr(f, "DFIRM_ID", "FIRM_PAN"))})
        if out:
            ledger.from_dataset(
                sorted({o["zone"] for o in out if o["zone"]}),
                f"FEMA flood zones intersecting the ring",
                url=f"{NFHL}/{NFHL_FLOOD_ZONE_LAYER}", layer="Flood Hazard Zones")
    return out, res


# -- overlays --------------------------------------------------------------

def overlays(ring, ledger: Ledger) -> tuple[dict[str, list[Any]], list[FetchResult]]:
    """Loess Hills, airport hazard and the two-mile city review area.

    Slope Protection is fetched but handled carefully: its county-wide feature
    covers ~949 sq mi of a 959 sq mi county with sloperange '0-30%', so a bare
    intersection means almost nothing. Only features whose type is not the
    county-wide 'Impact' blanket are treated as a real overlay.
    """
    out: dict[str, list[Any]] = {}
    results = []
    for key, service in (("loess_hills", LOESS_HILLS),
                         ("airport_hazard", AIRPORT_HAZARD),
                         ("two_mile_area", TWO_MILE)):
        res = _simple(service, ring, advisory=True)
        results.append(res)
        out[key] = _polys(res) if res.ok else []
        if out[key]:
            ledger.from_dataset(
                len(out[key]), f"{len(out[key])} {key.replace('_', ' ')} polygons in ring",
                url=f"{service}/0", layer=key)

    sp = _simple(SLOPE_OVERLAY, ring, advisory=True)
    results.append(sp)
    keep = []
    if sp.ok:
        for f in sp.features:
            gj = f.get("geometry")
            if not gj:
                continue
            rng = (clean(first_attr(f, "sloperange")) or "").strip()
            typ = (clean(first_attr(f, "type")) or "").strip()
            if rng == "0-30%" and typ.lower() == "impact":
                sp.warnings.append(
                    "slope protection feature is the county-wide 0-30% 'Impact' "
                    "polygon; not treated as a parcel-specific overlay")
                continue
            try:
                keep.append(geojson_to_working(gj))
            except Exception:  # noqa: BLE001
                continue
    out["slope_protection"] = keep
    return out, results


def electric_providers(ring, ledger: Ledger) -> tuple[list[str], FetchResult]:
    res = _simple(ELECTRIC, ring, advisory=True)
    names = []
    if res.ok:
        names = sorted({clean(first_attr(f, "name")) or "" for f in res.features} - {""})
        if names:
            ledger.from_dataset(names, "Electric service providers covering the ring",
                                url=f"{ELECTRIC}/0", layer="ElectricServiceAreas")
    return names, res


def zoning_cases(ring, ledger: Ledger) -> tuple[list[dict], FetchResult]:
    """Zoning Document Index: rezoning case history, not ordinance text.

    The probe confirmed its fields are documenttype / approvedon /
    recordeddocnum / bosdocnum / planningcasenum, with no link to district text,
    so this does not supply ordinance citations. A case touching the ring is a
    flag that the zoning history is worth checking.
    """
    res = _simple(ZONING_DOC_INDEX, ring, advisory=True)
    out = []
    if res.ok:
        for f in res.features:
            out.append({"document_type": clean(first_attr(f, "documenttype")),
                        "approved_on": clean(first_attr(f, "approvedon")),
                        "case": clean(first_attr(f, "planningcasenum")),
                        "bos_doc": clean(first_attr(f, "bosdocnum"))})
        if out:
            ledger.from_dataset(len(out),
                                f"{len(out)} zoning case records intersect the ring",
                                url=f"{ZONING_DOC_INDEX}/0", layer="Zoning Document Index")
    return out, res
