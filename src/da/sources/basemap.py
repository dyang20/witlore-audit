"""Topographic and street/aerial basemaps from the USGS National Map.

The assignment asks for a topography map and a street/aerial overview showing
the search ring, and the human audits carry both. The tiles are public domain
and need no key.

Tiles are fetched through the cached-tile endpoint, /tile/{z}/{y}/{x}, which is
the one operation every cached ArcGIS map service is guaranteed to support,
rather than through /export, which some cached services refuse.

A basemap is decoration around evidence, never evidence itself, so nothing here
is allowed to fail a run or hold one up: requests are short, they run together,
the first failure cancels the rest, and a failure is remembered for the life of
the process so a three-site session pays for an unreachable server once. The
caller gets None and the report says the map is missing and how to get one.
"""

from __future__ import annotations

import io
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

import requests

from .base import USER_AGENT

BASE = "https://basemap.nationalmap.gov/arcgis/rest/services"
# kind -> services to try in order, each (name, deepest zoom to ask for).
# Cached services are not uniformly deep: USGSImageryTopo returned 404 at zoom
# 15 over Pottawattamie County while USGSTopo served it, so each kind steps
# down a few zoom levels and then falls back to a second service.
SERVICES = {
    "topo": [("USGSTopo", 16)],
    "street": [("USGSImageryTopo", 16), ("USGSImageryOnly", 16)],
}
ATTRIBUTION = "Basemap: USGS The National Map"

TILE_PX = 256
ORIGIN_M = math.pi * 6378137.0           # half the Web Mercator world width
TILE_TIMEOUT = 8
MAX_TILES_ACROSS = 4
ZOOM_STEPS_DOWN = 3                      # on a missing tile, try this many coarser levels

# Per kind, set on failure; see the module docstring. Per kind because one
# service missing a tile says nothing about another service.
_FAILED: dict[str, str] = {}


class _MissingTile(Exception):
    """The server answered 404 for a tile: this level is not cached here."""


@dataclass
class Mosaic:
    image: Any                                   # PIL.Image, RGB
    extent: tuple[float, float, float, float]    # xmin, xmax, ymin, ymax (EPSG:3857)
    kind: str
    zoom: int
    url: str
    note: Optional[str] = None      # set when a fallback zoom or service was used


def lonlat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    x = math.radians(lon) * 6378137.0
    y = math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * 6378137.0
    return x, y


def _zoom_for(width_m: float, max_zoom: int) -> int:
    """Deepest zoom at which the view is at most MAX_TILES_ACROSS tiles wide."""
    for z in range(max_zoom, 0, -1):
        tile_m = 2.0 * ORIGIN_M / (2 ** z)
        if width_m / tile_m <= MAX_TILES_ACROSS:
            return z
    return 1


def last_failure(kind: Optional[str] = None) -> Optional[str]:
    if kind is not None:
        return _FAILED.get(kind)
    return "; ".join(f"{k}: {v}" for k, v in _FAILED.items()) or None


def fetch(kind: str, lon: float, lat: float, half_width_ft: float) -> Optional[Mosaic]:
    """A stitched basemap centred on (lon, lat), or None if it cannot be had.

    `half_width_ft` is ground distance from the centre to the edge of the view.
    Tries each service for the kind, and on a missing tile steps down to a
    coarser zoom before giving up on that service. Any other failure (no
    route, timeout, 5xx) gives up on the kind at once.
    """
    if kind in _FAILED or kind not in SERVICES:
        return None
    try:
        from PIL import Image
    except ImportError as e:                      # Pillow ships with matplotlib
        _FAILED[kind] = f"Pillow is not installed: {e}"
        return None

    half_m = (half_width_ft * 0.3048) / max(math.cos(math.radians(lat)), 0.2)
    first_z: Optional[int] = None
    misses: list[str] = []
    for service, max_zoom in SERVICES[kind]:
        z0 = _zoom_for(2.0 * half_m, max_zoom)
        first_z = first_z if first_z is not None else z0
        for z in range(z0, max(z0 - ZOOM_STEPS_DOWN, 0), -1):
            try:
                m = _mosaic(Image, service, z, lon, lat, half_m, kind)
            except _MissingTile as e:
                misses.append(f"{service} z{z}: {e}")
                continue
            except Exception as e:  # noqa: BLE001 - any other failure means no basemap
                _FAILED[kind] = f"{service}: {type(e).__name__}: {str(e)[:140]}"
                return None
            if m is not None:
                if z < z0 or service != SERVICES[kind][0][0]:
                    m.note = (f"{service} at zoom {z}; finer levels were not "
                              f"cached for this area")
                return m
    _FAILED[kind] = "no cached tiles: " + "; ".join(misses)[:300]
    return None


def _mosaic(Image, service: str, z: int, lon: float, lat: float,
            half_m: float, kind: str) -> Optional[Mosaic]:
    cx, cy = lonlat_to_mercator(lon, lat)
    tile_m = 2.0 * ORIGIN_M / (2 ** z)
    x0 = int(math.floor((cx - half_m + ORIGIN_M) / tile_m))
    x1 = int(math.floor((cx + half_m + ORIGIN_M) / tile_m))
    y0 = int(math.floor((ORIGIN_M - (cy + half_m)) / tile_m))
    y1 = int(math.floor((ORIGIN_M - (cy - half_m)) / tile_m))
    jobs = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if len(jobs) > (MAX_TILES_ACROSS + 1) ** 2:
        raise RuntimeError(f"view would need {len(jobs)} tiles")

    root = f"{BASE}/{service}/MapServer"

    def one(xy: tuple[int, int]):
        x, y = xy
        r = requests.get(f"{root}/tile/{z}/{y}/{x}", timeout=TILE_TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        if r.status_code == 404:
            raise _MissingTile(f"tile {y}/{x} not cached")
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")

    with ThreadPoolExecutor(max_workers=8) as pool:
        tiles = list(pool.map(one, jobs))

    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    sheet = Image.new("RGB", (cols * TILE_PX, rows * TILE_PX))
    for (x, y), tile in zip(jobs, tiles):
        if tile.size != (TILE_PX, TILE_PX):
            tile = tile.resize((TILE_PX, TILE_PX))
        sheet.paste(tile, ((x - x0) * TILE_PX, (y - y0) * TILE_PX))

    extent = (x0 * tile_m - ORIGIN_M, (x1 + 1) * tile_m - ORIGIN_M,
              ORIGIN_M - (y1 + 1) * tile_m, ORIGIN_M - y0 * tile_m)
    return Mosaic(image=sheet, extent=extent, kind=kind, zoom=z, url=root)
