"""End-to-end smoke test with every network source stubbed.

Proves the full path runs and produces a .docx with maps and a provenance
appendix, without touching a county server. Run it before a demo: if this
passes and the probe passes, the live run should work.

    python3 tests/smoke_offline.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shapely.geometry import Point, Polygon

from da import pipeline
from da.evidence import Ledger
from da.geo.crs import point_to_working
from da.models import Parcel, SiteInput
from da.report.build import build
from da.sources.base import FetchResult

SITE = SiteInput(
    opportunity_id="TI-OPP-35820-SMOKE", latitude=41.27277, longitude=-95.50833,
    search_ring_radius_ft=3281, requested_rad_ft=450, state="IA",
    relo_site_fa_number="10589315", customer_market="NORTHERN PLAINS",
)
CX, CY = point_to_working(SITE.longitude, SITE.latitude)


def square(dx, dy, half):
    return Polygon([(CX + dx - half, CY + dy - half), (CX + dx + half, CY + dy - half),
                    (CX + dx + half, CY + dy + half), (CX + dx - half, CY + dy + half)])


def fake_parcels(ring, ledger: Ledger):
    """Five parcels spanning the outcomes: large farm, mid, small, wetland-hit,
    and one with an assessed dwelling."""
    specs = [
        ("754019100003", "HERING, NANCY J LIFE ESTATE", square(0, 0, 1100), 33.0, 0.0),
        ("754019100004", "SCHNEIDER FARMS LLC", square(-1600, 900, 900), 24.5, 0.0),
        ("754019100005", "BOYER VALLEY TRUST", square(1500, 1200, 500), 11.2, 0.0),
        ("754019100006", "OLSEN, MARK & DIANE", square(1200, -1600, 700), 18.0, 142300.0),
        ("754019100007", "CARSTENS, R", square(-900, -1800, 260), 3.1, 0.0),
    ]
    out = []
    for pin, owner, geom, acres, dwell in specs:
        p = Parcel(pin=pin, owner=owner, geometry=geom, net_acres=acres,
                   prop_class="A", dwelling_value=dwell,
                   owner_address="36695 HWY 6, OAKLAND, IA 51560",
                   city_code=None, legal="BELKNAP TWP 19-75-40 SW NW",
                   area_in_ring_sqft=geom.intersection(ring.polygon).area)
        p.evidence_ids.append(ledger.from_dataset(
            {"pin": pin, "owner": owner, "net_acres": acres},
            f"Parcel {pin} ownership and acreage",
            url="https://gis.pottcounty-ia.gov/.../PublicData_Parcels/FeatureServer/0",
            layer="PublicData_Parcels"))
        p._attr_zoning = {"rural": "A-2", "council_bluffs": None}
        out.append(p)
    return out, FetchResult(ok=True, count=len(out), layer="Parcels")


def fake_districts(ring, ledger: Ledger):
    big = Point(CX, CY).buffer(ring.radius_ft * 1.4)
    loess = square(-1600, 900, 1000)
    return ([
        {"jurisdiction": "Pottawattamie County", "code": "A-2",
         "name": "Agricultural Production", "geometry": big.difference(loess),
         "service": "https://gis.pottcounty-ia.gov/.../Zoning_Pottawattamie/FeatureServer",
         "ordinance_link": None, "ordinance_number": None},
        {"jurisdiction": "Pottawattamie County", "code": "A-4",
         "name": "Loess Hills", "geometry": loess,
         "service": "https://gis.pottcounty-ia.gov/.../Zoning_Pottawattamie/FeatureServer",
         "ordinance_link": None, "ordinance_number": None},
    ], [FetchResult(ok=True, count=2, layer="Zoning")])


def fake_wetlands(ring, ledger: Ledger):
    ledger.from_dataset(2, "2 NWI wetland polygons intersect the ring",
                        url="https://www.fws.gov/wetlandsmapservice/.../0",
                        layer="Wetlands")
    return [square(1500, 1200, 430), square(-500, 1900, 300)], FetchResult(ok=True, count=2)


def fake_flood(ring, ledger: Ledger):
    ledger.from_dataset(["AE"], "FEMA flood zones intersecting the ring",
                        url="https://hazards.fema.gov/.../NFHL/MapServer/28",
                        layer="Flood Hazard Zones")
    return [{"geometry": square(1200, -1600, 500), "zone": "AE",
             "panel": "19155C0150E"}], FetchResult(ok=True, count=1)


def fake_dwellings(ring, ledger: Ledger):
    return ({"footprints": [], "residential": [{"geometry": square(1200, -1600, 40),
                                                "taxpin": "754019100006", "cls": "R",
                                                "exempt": "N"}],
             "addresses": [{"geometry": Point(CX + 1210, CY - 1590),
                            "address": "35509 ELMTREE RD, CARSON, IA 51525"}]},
            [FetchResult(ok=True, count=1)])


def fake_boundaries(ring, ledger: Ledger):
    return [], FetchResult(ok=True, count=0, layer="Municipal Boundaries")


def fake_elevation(ring, ledger: Ledger):
    stats = {"centre_ft": 1043.0, "min_ft": 988.0, "max_ft": 1104.0,
             "range_ft": 116.0, "mean_ft": 1041.0, "n_samples": 17}
    ledger.from_dataset(stats, "Ground elevation across the ring: 988-1104 ft "
                               "(range 116 ft) from 17 USGS 3DEP samples",
                        url="https://epqs.nationalmap.gov/v1/json", layer="3DEP")
    return 1043.0, stats


def fake_towers(ring, ledger: Ledger, **kw):
    from da.sources.asr import Tower, _record
    towers = [
        Tower("1234567", 41.2730, -95.5085, 480.0, "GTOWER", "AMERICAN TOWERS LLC",
              400.0, status="C", date_constructed="05/08/2000"),
        Tower("7654321", 41.2890, -95.4990, 310.0, "LTOWER", "CITYSWITCH II, LLC",
              7200.0, status="G"),
        Tower("1112223", 41.2600, -95.5300, 250.0, "TOWER", "OLD CARRIER INC",
              9100.0, status="I", date_constructed="03/01/1979",
              date_dismantled="07/11/2006"),
    ]
    _record(ledger, towers, 1)
    return towers, None


def fake_overlays(ring, ledger: Ledger):
    """One Loess Hills polygon in the north-east, nothing else."""
    hills = square(1500, 1500, 900)
    ledger.from_dataset(1, "1 loess hills polygons in ring",
                        url="https://gis.pottcounty-ia.gov/.../Loess_Hills/0",
                        layer="loess_hills")
    return ({"loess_hills": [hills], "airport_hazard": [], "two_mile_area": [],
             "slope_protection": []},
            [FetchResult(ok=True, count=1)])


def fake_electric(ring, ledger: Ledger):
    ledger.from_dataset(["MidAmerican Energy"],
                        "Electric service providers covering the ring",
                        url="https://gis.pottcounty-ia.gov/.../Electric/0",
                        layer="ElectricServiceAreas")
    return ["MidAmerican Energy"], FetchResult(ok=True, count=1)


def fake_cases(ring, ledger: Ledger):
    return [], FetchResult(ok=True, count=0)


def fake_basemap(kind, lon, lat, half_width_ft):
    """A flat tinted sheet standing in for USGS tiles, with the right extent."""
    import math
    from PIL import Image
    from da.sources import basemap
    cx, cy = basemap.lonlat_to_mercator(lon, lat)
    half_m = (half_width_ft * 0.3048) / math.cos(math.radians(lat))
    tint = (226, 232, 216) if kind == "topo" else (58, 74, 52)
    return basemap.Mosaic(image=Image.new("RGB", (512, 512), tint),
                          extent=(cx - half_m, cx + half_m, cy - half_m, cy + half_m),
                          kind=kind, zoom=15,
                          url="https://basemap.nationalmap.gov/(stubbed)")


def main() -> int:
    pipeline.parcels_src.fetch = fake_parcels
    pipeline.zoning_src.fetch_districts = fake_districts
    pipeline.ctx.wetlands = fake_wetlands
    pipeline.ctx.flood_zones = fake_flood
    pipeline.ctx.dwellings = fake_dwellings
    pipeline.ctx.municipal_boundaries = fake_boundaries
    pipeline.elev.sample_ring = fake_elevation
    pipeline.asr_src.towers_near = fake_towers
    pipeline.ctx.overlays = fake_overlays
    pipeline.ctx.electric_providers = fake_electric
    pipeline.ctx.zoning_cases = fake_cases
    from da.sources import basemap
    basemap.fetch = fake_basemap

    out_dir = "outputs/smoke"
    report, ledger = pipeline.run(
        SITE, out_dir=out_dir, ordinance_path="data/ordinance.txt")

    print()
    print(f"recommendation : {report.recommendation()}")
    print(f"zoning level   : {report.zoning_level.level if report.zoning_level else '?'}"
          f" - {report.zoning_level.rationale[:70] if report.zoning_level else ''}")
    print(f"evidence       : {len(ledger)} records")
    print(f"verified rules : {len(report.rules)}")
    print(f"unresolved     : {len(report.unresolved)}")
    print()
    print(f"{'':2} {'owner':28} {'acres':>6} {'district':9} {'buildable':>11}  disposition")
    for p in sorted(report.parcels, key=lambda q: q.letter or "Z"):
        b = f"{p.buildable_sqft:,.0f}" if p.buildable_sqft is not None else "-"
        print(f"{p.letter:2} {(p.owner or '')[:28]:28} {p.net_acres or 0:6.1f} "
              f"{','.join(d.code for d in p.districts):9} {b:>11}  "
              f"{p.disposition.value:14} {','.join(p.reason_codes)}")
    print()
    for u in report.unresolved:
        print(f"  UNRESOLVED: {u.what_is_missing[:100]}")

    site_dir = os.path.join(out_dir, SITE.opportunity_id)
    os.makedirs(site_dir, exist_ok=True)
    path = os.path.join(site_dir, f"Desktop Audit - {SITE.opportunity_id}.docx")
    build(report, ledger, path, assumptions=report.assumptions)
    ledger.to_json(os.path.join(site_dir, "evidence.json"))
    print(f"\nwrote {path} ({os.path.getsize(path):,} bytes)")
    for k, v in report.map_paths.items():
        print(f"  map {k}: {v} ({os.path.getsize(v):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
