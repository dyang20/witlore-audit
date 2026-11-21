"""Coordinate handling.

All measurement happens in EPSG:102676 (NAD83 Iowa South State Plane, US survey
feet), confirmed as the native CRS of the county parcel, zoning and building
services. Working in feet means setbacks, fall distances and compound sizes are
compared in the units the ordinance uses, with no conversion at the point of
decision.

Server-reported areas are never trusted: the same building footprint reports
1,019.8 in one county service and 167.9 in another, a ratio consistent with one
of them serving Web Mercator, where the scale distortion at latitude 41.3 is
1/cos(41.3) ~ 1.33. Areas are computed here from geometry instead.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pyproj import CRS, Transformer
from shapely.geometry import shape as shapely_shape
from shapely.ops import transform as shapely_transform

# The ArcGIS services report wkid 102676 with latestWkid 3418.
WORKING_EPSG = 3418          # NAD83 / Iowa South (ftUS)
WORKING_SRID_ESRI = 102676   # what the ArcGIS endpoints call it
WGS84_EPSG = 4326

SQFT_PER_ACRE = 43_560.0


@lru_cache(maxsize=8)
def _transformer(src: int, dst: int) -> Transformer:
    return Transformer.from_crs(CRS.from_epsg(src), CRS.from_epsg(dst), always_xy=True)


def to_working(geom: Any, src_epsg: int = WGS84_EPSG) -> Any:
    if src_epsg == WORKING_EPSG:
        return geom
    t = _transformer(src_epsg, WORKING_EPSG)
    return shapely_transform(lambda x, y, z=None: t.transform(x, y), geom)


def to_wgs84(geom: Any, src_epsg: int = WORKING_EPSG) -> Any:
    if src_epsg == WGS84_EPSG:
        return geom
    t = _transformer(src_epsg, WGS84_EPSG)
    return shapely_transform(lambda x, y, z=None: t.transform(x, y), geom)


def to_web_mercator(geom: Any) -> Any:
    """Working CRS to EPSG:3857. For drawing over basemap tiles only; nothing is
    ever measured in Web Mercator, where distances stretch by 1/cos(latitude)."""
    t = _transformer(WORKING_EPSG, 3857)
    return shapely_transform(lambda x, y, z=None: t.transform(x, y), geom)


def point_to_working(lon: float, lat: float) -> tuple[float, float]:
    return _transformer(WGS84_EPSG, WORKING_EPSG).transform(lon, lat)


def point_to_wgs84(x: float, y: float) -> tuple[float, float]:
    return _transformer(WORKING_EPSG, WGS84_EPSG).transform(x, y)


def geojson_to_working(geojson_geom: dict, src_epsg: int = WGS84_EPSG) -> Any:
    """GeoJSON geometry dict -> shapely geometry in the working CRS."""
    return to_working(shapely_shape(geojson_geom), src_epsg)


def area_sqft(geom: Any) -> float:
    """Area from geometry in the working CRS. Never read an area attribute."""
    return float(geom.area)


def area_acres(geom: Any) -> float:
    return area_sqft(geom) / SQFT_PER_ACRE
