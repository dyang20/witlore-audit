"""Buildable area and compound fit: the geometric test that decides a parcel.

    B = (P n R) (-) s - W - D

where P is the parcel, R the search ring, s the applicable setback, W wetlands
and D dwelling buffers. Flood zones are NOT subtracted: Tillman treats a FEMA
zone as a risk to flag, not an automatic disqualifier.

A parcel passes when a compound square fits inside B. Two tiers, per Tillman:
100x100 ft is standard, 80x80 has been approved on special occasions. An 80x80
pass is reported as Pursue with a stated exception needing approval, never as a
silent downgrade: the footprint drops from 10,000 to 6,400 sq ft, a 36% cut, and
on tight urban rings several parcels sit in exactly that band.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from shapely.affinity import rotate, translate
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

# Half-diagonal of a square of side c. Eroding by this guarantees the square
# fits at any rotation; eroding by c/2 is the necessary condition. Between the
# two, we search.
def _half_diagonal(side: float) -> float:
    return side * math.sqrt(2.0) / 2.0


@dataclass
class CompoundFit:
    fits: bool
    size_ft: Optional[float] = None
    center: Optional[tuple[float, float]] = None
    rotation_deg: float = 0.0
    method: str = ""          # guaranteed | searched | rejected
    buildable_sqft: float = 0.0


def _square(side: float, cx: float, cy: float, rotation_deg: float = 0.0) -> Polygon:
    h = side / 2.0
    sq = Polygon([(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)])
    return rotate(sq, rotation_deg, origin=(cx, cy)) if rotation_deg else sq


def buildable_region(
    parcel_geom: Any,
    ring_polygon: Any,
    setback_ft: float = 0.0,
    exclusions: Optional[Iterable[Any]] = None,
) -> Any:
    """B = (P n R) (-) s - exclusions. Returns a (possibly empty) geometry."""
    region = parcel_geom.intersection(ring_polygon)
    if region.is_empty:
        return region
    if setback_ft and setback_ft > 0:
        # Setbacks are measured from the parcel boundary, not the ring edge, so
        # erode the parcel first and then re-clip to the ring.
        eroded = parcel_geom.buffer(-float(setback_ft))
        region = eroded.intersection(ring_polygon) if not eroded.is_empty else eroded
    blockers = [g for g in (exclusions or []) if g is not None and not g.is_empty]
    if blockers and not region.is_empty:
        region = region.difference(unary_union(blockers))
    return region


def fit_compound(region: Any, size_ft: float, *, search_steps: int = 12,
                 rotations: Iterable[float] = (0, 15, 30, 45, 60, 75)) -> CompoundFit:
    """Can a square of `size_ft` sit inside `region`?

    Fast path: erode by the half-diagonal. If something survives, the square
    fits at any rotation and we return its representative point.
    Reject path: erode by size/2. If nothing survives, no square can fit.
    Otherwise sample candidate centres and test real squares.
    """
    area = float(region.area) if region and not region.is_empty else 0.0
    if area <= 0:
        return CompoundFit(False, method="rejected", buildable_sqft=0.0)
    if area < size_ft ** 2:
        # Cannot fit a square whose area exceeds the region's.
        return CompoundFit(False, method="rejected", buildable_sqft=area)

    guaranteed = region.buffer(-_half_diagonal(size_ft))
    if not guaranteed.is_empty:
        p = guaranteed.representative_point()
        return CompoundFit(True, size_ft, (p.x, p.y), 0.0, "guaranteed", area)

    necessary = region.buffer(-size_ft / 2.0)
    if necessary.is_empty:
        return CompoundFit(False, method="rejected", buildable_sqft=area)

    minx, miny, maxx, maxy = necessary.bounds
    xs = _linspace(minx, maxx, search_steps)
    ys = _linspace(miny, maxy, search_steps)
    for cx in xs:
        for cy in ys:
            if not necessary.contains(Point(cx, cy)):
                continue
            for rot in rotations:
                if region.contains(_square(size_ft, cx, cy, rot)):
                    return CompoundFit(True, size_ft, (cx, cy), float(rot),
                                       "searched", area)
    return CompoundFit(False, method="rejected", buildable_sqft=area)


def best_compound_fit(region: Any, primary_ft: float = 100.0,
                      exception_ft: float = 80.0) -> CompoundFit:
    """Try the standard compound first, then the exception size."""
    fit = fit_compound(region, primary_ft)
    if fit.fits:
        return fit
    if exception_ft and exception_ft < primary_ft:
        alt = fit_compound(region, exception_ft)
        if alt.fits:
            return alt
    return CompoundFit(False, method="rejected",
                       buildable_sqft=float(region.area) if region and not region.is_empty else 0.0)


def guy_anchors_fit(parcel_geom: Any, center: tuple[float, float],
                    anchor_radius_ft: float, setback_ft: float = 0.0,
                    exclusions: Optional[Iterable[Any]] = None) -> bool:
    """Guyed towers need the whole anchor circle on usable land.

    The DA training asks specifically whether anchors can be placed without
    entering wetland, so exclusions are applied here too. Anchors must be on the
    parcel but need not be inside the search ring: only the base must be.
    """
    if anchor_radius_ft <= 0:
        return True
    usable = parcel_geom.buffer(-float(setback_ft)) if setback_ft else parcel_geom
    if usable.is_empty:
        return False
    blockers = [g for g in (exclusions or []) if g is not None and not g.is_empty]
    if blockers:
        usable = usable.difference(unary_union(blockers))
    circle = Point(*center).buffer(float(anchor_radius_ft), resolution=32)
    return bool(usable.contains(circle))


def dwelling_buffers(dwelling_points: Iterable[Any], radius_ft: float) -> list[Any]:
    """Pottawattamie Sec. 8.004.220.13: a tower must stand at least its own
    height from adjacent dwellings or structures not associated with the tower.
    Expressed as keep-out circles of that radius around each dwelling."""
    if radius_ft <= 0:
        return []
    return [g.buffer(float(radius_ft), resolution=16) for g in dwelling_points
            if g is not None and not g.is_empty]


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n <= 1 or hi <= lo:
        return [(lo + hi) / 2.0]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]
