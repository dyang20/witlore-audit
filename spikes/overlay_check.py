import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from da.geo.ring import Ring
from da.sources import context as c
from da.sources.base import query_layer

ring = Ring(41.27277, -95.50833, 3281)
services = [
    ("loess_hills", c.LOESS_HILLS),
    ("airport_hazard", c.AIRPORT_HAZARD),
    ("two_mile_area", c.TWO_MILE),
    ("slope_protection", c.SLOPE_OVERLAY),
]
for name, svc in services:
    r = query_layer(svc, 0, envelope_wgs84=ring.envelope_wgs84())
    status = "ok" if r.ok else "FAIL"
    print(f"{name:18} {status:5} n={r.count}  {r.error or ''}")
    if r.warnings:
        print(f"{'':18} warn: {r.warnings}")