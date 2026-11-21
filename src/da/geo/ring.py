"""Search ring geometry.

The ring is the hard constraint on where the tower base may sit: the assignment
requires the base inside the ring, and the DA training tells analysts to
double-check coordinates because ring drivers navigate to them.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import Point

from .crs import WGS84_EPSG, point_to_working, to_wgs84

MILES_TO_FT = 5280.0
# Resolution of the circle approximation. 128 segments on a 3,281 ft ring puts
# the chord error under a foot, well inside any setback tolerance.
RING_SEGMENTS = 128


class Ring:
    def __init__(self, lat: float, lon: float, radius_ft: float) -> None:
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError(f"coordinates out of range: {lat}, {lon}")
        if radius_ft <= 0:
            raise ValueError(f"ring radius must be positive, got {radius_ft}")
        self.lat = float(lat)
        self.lon = float(lon)
        self.radius_ft = float(radius_ft)
        self.center_x, self.center_y = point_to_working(lon, lat)
        self.center = Point(self.center_x, self.center_y)
        self.polygon = self.center.buffer(self.radius_ft, resolution=RING_SEGMENTS // 4)

    def buffer_ft(self, distance_ft: float) -> Any:
        """Concentric buffer, e.g. the 2-mile ASR search area."""
        return self.center.buffer(self.radius_ft + distance_ft,
                                  resolution=RING_SEGMENTS // 4)

    def radius_buffer(self, miles: float) -> Any:
        """Buffer measured from the ring centre, not its edge: the human reports'
        'towers within 2 miles' is measured from the centre."""
        return self.center.buffer(miles * MILES_TO_FT, resolution=RING_SEGMENTS // 4)

    def contains(self, geom: Any) -> bool:
        return bool(self.polygon.contains(geom))

    def envelope_wgs84(self, pad: float = 1.02) -> dict[str, float]:
        """Bounding box for ArcGIS spatial queries, in WGS84."""
        padded = self.center.buffer(self.radius_ft * pad, resolution=8)
        minx, miny, maxx, maxy = to_wgs84(padded).bounds
        return {"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                "spatialReference": {"wkid": WGS84_EPSG}}

    def area_sqft(self) -> float:
        return float(self.polygon.area)

    def verify(self) -> list[str]:
        """Assertions the pipeline runs before using the ring. Returns problems."""
        problems = []
        back_lon, back_lat = to_wgs84(self.center).coords[0]
        if abs(back_lat - self.lat) > 1e-6 or abs(back_lon - self.lon) > 1e-6:
            problems.append(
                f"ring centre failed round-trip: {self.lat},{self.lon} -> "
                f"{back_lat},{back_lon}")
        # A circle of radius r has area pi*r^2; the polygon approximation should
        # be within a fraction of a percent.
        import math
        expected = math.pi * self.radius_ft ** 2
        if abs(self.polygon.area - expected) / expected > 0.001:
            problems.append(
                f"ring area {self.polygon.area:.0f} differs from pi*r^2 {expected:.0f}")
        return problems

    def __repr__(self) -> str:
        return (f"Ring(lat={self.lat}, lon={self.lon}, "
                f"radius_ft={self.radius_ft:.0f})")
