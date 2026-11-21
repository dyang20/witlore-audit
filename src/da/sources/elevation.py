"""Ground elevation from the USGS 3DEP point query service.

Two uses:
  - the ground delta between the existing site and a candidate, which feeds
    the RAD-to-height adjustment
  - the terrain narrative ("drops up to 120 ft in some areas" in the 34659 DA)

Elevation matters for zoning, not just description. The DA training notes a site
sitting 30-50 ft above surrounding grade can push an otherwise compliant tower
past a district's height cap. Where a district has a cap, apply.py compares
against the height including the ground delta.
"""

from __future__ import annotations

from statistics import mean
from typing import Optional

import requests

from ..evidence import Ledger
from ..geo.crs import point_to_wgs84
from .base import USER_AGENT, SourceError

EPQS = "https://epqs.nationalmap.gov/v1/json"
NO_DATA = -1000000.0
# Concurrent EPQS requests. This is the fallback path, so it wants to finish
# quickly rather than tread lightly; EPQS has been observed at 8 to 20 seconds
# a call, and seventeen points in series was the slowest stage of a run.
MAX_PARALLEL = 12

# The 3DEP ImageServer samples many points in ONE request, where EPQS takes one
# request per point. Seventeen EPQS calls at ~8s each was the slowest stage of
# a run even parallelised; this collapses them into a single call.
#
# UNITS. EPQS is asked for Feet and returns feet. The ImageServer returns the
# raster's native units, which for 3DEP is METRES. Mixing the two is a silent
# 3.28x error that would land on tower height, so the batch result is
# cross-checked against one EPQS reading before it is trusted. This project has
# already been bitten once by exactly this, with ASR structure heights.
IMAGE_SERVER = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
                "3DEPElevation/ImageServer/getSamples")
METRES_TO_FT = 3.28084
# How far the batch's centre reading may sit from the EPQS reading before the
# batch is discarded. Generous enough for interpolation differences between the
# two services, far tighter than a unit error.
UNIT_CHECK_TOLERANCE_FT = 25.0
# Short on purpose. The batch is an optimisation, and a slow failure is worse
# than no attempt: the run still has to do the per-point fallback afterwards,
# so every second spent waiting here is added to the old cost rather than
# replacing it.
BATCH_TIMEOUT = 12

# Set once the batch has failed in this process, so a three-site demo pays the
# timeout once rather than once per site. Cleared only by a restart, which is
# the right granularity: if the service was down a minute ago it is unlikely
# to be worth re-testing mid-review.
_BATCH_FAILED: Optional[str] = None


def elevation_ft(lat: float, lon: float, timeout: int = 30) -> Optional[float]:
    try:
        r = requests.get(EPQS, params={"x": lon, "y": lat, "units": "Feet",
                                       "wkid": 4326, "includeDate": "false"},
                         timeout=timeout, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001
        return None
    for key in ("value", "elevation"):
        if key in data:
            try:
                v = float(data[key])
            except (TypeError, ValueError):
                continue
            return None if v <= NO_DATA else v
    loc = (data.get("USGS_Elevation_Point_Query_Service") or {}).get("Elevation_Query")
    if isinstance(loc, dict) and "Elevation" in loc:
        try:
            v = float(loc["Elevation"])
            return None if v <= NO_DATA else v
        except (TypeError, ValueError):
            return None
    return None


def sample_points_batch(points: list[tuple[float, float]],
                        timeout: int = BATCH_TIMEOUT
                        ) -> Optional[list[Optional[float]]]:
    """Elevations in feet for many (lat, lon) points in one request.

    Returns a list positionally aligned with `points`, or None if the service
    could not be used at all, which tells the caller to fall back to EPQS.

    Samples come back keyed by `locationId` and NoData points may be dropped
    entirely, so results are placed by that id rather than by arrival order.
    """
    import json

    global _BATCH_FAILED
    if _BATCH_FAILED:
        return None

    geometry = {"points": [[lon, lat] for lat, lon in points],
                "spatialReference": {"wkid": 4326}}
    try:
        r = requests.post(
            IMAGE_SERVER,
            data={"geometry": json.dumps(geometry),
                  "geometryType": "esriGeometryMultipoint",
                  "returnFirstValueOnly": "true",
                  "interpolation": "RSP_BilinearInterpolation",
                  "f": "json"},
            timeout=timeout, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001 - any failure means fall back
        _BATCH_FAILED = f"{type(e).__name__}: {str(e)[:120]}"
        return None
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        _BATCH_FAILED = f"service error {err.get('code')}: {err.get('message')}"
        return None
    samples = data.get("samples") if isinstance(data, dict) else None
    if not isinstance(samples, list) or not samples:
        _BATCH_FAILED = "no samples in the response"
        return None

    out: list[Optional[float]] = [None] * len(points)
    for s in samples:
        try:
            idx = int(s.get("locationId"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(points):
            continue
        raw = s.get("value")
        if raw is None:
            continue
        # A multiband sample is comma separated; elevation is single band.
        first = str(raw).split(",")[0].strip()
        if not first or first.lower() == "nodata":
            continue
        try:
            metres = float(first)
        except ValueError:
            continue
        if metres <= NO_DATA:
            continue
        out[idx] = metres * METRES_TO_FT
    return out if any(v is not None for v in out) else None


def sample_ring(ring, ledger: Ledger, *, rings: int = 2, per_ring: int = 8
                ) -> tuple[Optional[float], dict]:
    """Centre elevation plus a coarse sample for the terrain narrative.

    Returns (centre_elevation_ft, stats). Stats carry min/max/range so the
    report can say how much the ground moves across the ring, as the human
    reports do.
    """
    import math
    from concurrent.futures import ThreadPoolExecutor

    # One EPQS round trip per point. Seventeen of them in series was the
    # single slowest stage of a run, and the points are independent, so they
    # go out together. The pool stays small: this is a free public service and
    # hammering it is both rude and a good way to get throttled mid-demo.
    points: list[tuple[float, float]] = [(ring.lat, ring.lon)]
    for r in range(1, rings + 1):
        radius = ring.radius_ft * r / rings
        for i in range(per_ring):
            theta = 2 * math.pi * i / per_ring
            x = ring.center_x + radius * math.cos(theta)
            y = ring.center_y + radius * math.sin(theta)
            lon, lat = point_to_wgs84(x, y)
            points.append((lat, lon))

    # One batch request for every point, and one EPQS reading of the centre,
    # at the same time. The EPQS reading does double duty: it is the value the
    # tower height is derived from, on the path this pipeline has used all
    # along, and it is the check that the batch came back in the units
    # expected. Running them together costs nothing, since the batch is much
    # the faster of the two.
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_batch = pool.submit(sample_points_batch, points)
        f_centre = pool.submit(elevation_ft, ring.lat, ring.lon)
        batch = f_batch.result()
        centre = f_centre.result()

    method = "USGS 3DEP ImageServer getSamples (one request)"
    used_batch = True
    values: list[Optional[float]]
    if batch is None:
        used_batch = False
        why = f" (batch unavailable: {_BATCH_FAILED})" if _BATCH_FAILED else ""
        method = f"USGS 3DEP point query, one request per point{why}"
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            values = list(pool.map(lambda p: elevation_ft(*p), points[1:]))
        values = [centre] + values
    elif (centre is not None and batch[0] is not None
            and abs(batch[0] - centre) > UNIT_CHECK_TOLERANCE_FT):
        # The two services disagree about the same point. A ratio near 3.28
        # means the batch came back in metres and the conversion is wrong;
        # anything else means one of them is unreliable here. Either way the
        # batch is not trustworthy, so it is discarded rather than reconciled.
        used_batch = False
        method = (f"USGS 3DEP point query, one request per point "
                  f"(batch discarded: centre {batch[0]:.0f} ft vs point query "
                  f"{centre:.0f} ft)")
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            values = list(pool.map(lambda p: elevation_ft(*p), points[1:]))
        values = [centre] + values
    else:
        values = list(batch)
        if centre is not None:
            values[0] = centre

    centre = values[0]
    samples = [v for v in values if v is not None]

    if not samples:
        return None, {"error": "no elevation samples returned"}

    stats = {
        "centre_ft": centre,
        "min_ft": min(samples),
        "max_ft": max(samples),
        "range_ft": max(samples) - min(samples),
        "mean_ft": mean(samples),
        "n_samples": len(samples),
    }
    stats["method"] = method
    ledger.from_dataset(
        stats,
        f"Ground elevation across the ring: {stats['min_ft']:.0f}-{stats['max_ft']:.0f} ft "
        f"(range {stats['range_ft']:.0f} ft) from {stats['n_samples']} USGS 3DEP samples "
        f"via {method}",
        url=IMAGE_SERVER if used_batch else EPQS,
        layer="USGS 3DEP",
    )
    return centre, stats


def ground_delta_ft(candidate_lat: float, candidate_lon: float,
                    reference_elevation_ft: Optional[float]) -> float:
    """Elevation of a candidate relative to the reference, 0.0 when unknown.

    Returning 0.0 is deliberate: an unknown delta must not silently inflate or
    deflate the tower height. The caller records an Unresolved when this
    happens, so the assumption is visible rather than buried.
    """
    if reference_elevation_ft is None:
        return 0.0
    v = elevation_ft(candidate_lat, candidate_lon)
    if v is None:
        return 0.0
    return v - reference_elevation_ft
